import torch, torchvision, utils
from torch import nn
from torch.nn import functional as F

class RoIHead(nn.Module):
    # Класифікація та регресія за заданими ознаками. 
    def __init__(self, output_size, downsample, backbone_fc, out_channels, num_classes, gpu_id):
        super(RoIHead, self).__init__()
        torch.cuda.set_device(gpu_id)
        self.gpu = gpu_id
        
        self.output_size = output_size
        self.downsample = downsample
        
        self.num_classes = num_classes
        self.fc = backbone_fc
        self.classification = nn.Linear(out_channels, num_classes).cuda(self.gpu)
        self.bbox_regressor = nn.Linear(out_channels, 4 * num_classes).cuda(self.gpu)
        
        self._initialize_weights()

    # Прямий хід через голову RoI для класифікації та регресії обмежувальної рамки.
    def forward(self, images, features, proposals):
        N, C, f_h, f_w = features.shape

        proposals_list = [proposal for proposal in proposals]
        bbox_features = torchvision.ops.roi_pool(features, proposals_list, self.output_size, 1 / self.downsample)
        bbox_features = bbox_features.view(N, -1, C, self.output_size, self.output_size)
        
        bbox_features = torch.flatten(bbox_features, start_dim=2)
        bbox_features = self.fc(bbox_features)
        
        objectness = self.classification(bbox_features)
        pred_bbox_deltas = self.bbox_regressor(bbox_features)
        return objectness, pred_bbox_deltas
    
    # Ініціалізує вагові коефіцієнти для шарів класифікації та регресора bbox.
    def _initialize_weights(self):
        nn.init.normal_(self.classification.weight, 0, 0.01)
        nn.init.normal_(self.bbox_regressor.weight, 0, 0.001)
        
        nn.init.constant_(self.classification.bias, 0)
        nn.init.constant_(self.bbox_regressor.bias, 0)
        
class FastRCNN(nn.Module):
    def __init__(self, roi_head,
                 bbox_reg_weights,
                 iou_positive_thresh, iou_negative_high, iou_negative_low,
                 batch_size_per_image, positive_fraction,
                 min_size, nms_thresh, 
                 score_thresh, top_n,
                 loss_type='ce_smoothl1'): 
        super(FastRCNN, self).__init__()
        self.roi_head = roi_head
        self.num_classes = roi_head.num_classes
        
        self.box_coder = utils.BoxCoder(bbox_reg_weights)
        self.proposal_matcher = utils.Matcher(iou_positive_thresh, iou_negative_high, iou_negative_low, low_quality_match=False)
        self.sampler = utils.Balanced_Sampler(batch_size_per_image, positive_fraction)
        
        self.min_size = min_size
        self.nms_thresh = nms_thresh
        self.score_thresh = score_thresh
        self.top_n = top_n
        self.loss_type = loss_type

    # Призначає пропозиціям позначки правдивості та обмежувальні рамки.
    def assign_gt_to_proposals(self, proposals, gt_labels, gt_bboxs):
        labels, matched_gt_bboxs = [], []
        for proposals_per_image, gt_labels_per_image, gt_bboxs_per_image in zip(proposals, gt_labels, gt_bboxs):
            match_quality_matrix = torchvision.ops.box_iou(gt_bboxs_per_image, proposals_per_image)
            matched_idxs_per_image = self.proposal_matcher(match_quality_matrix)
            
            clamped_matched_idxs_per_image = torch.clamp(matched_idxs_per_image, min=0)
            
            labels_per_image = gt_labels_per_image[clamped_matched_idxs_per_image]
            matched_gt_bboxs_per_image = gt_bboxs_per_image[clamped_matched_idxs_per_image]
            
            # Присвоїти негативні мітки
            negative_idxs = matched_idxs_per_image == self.proposal_matcher.BELOW_LOW_THRESHOLD
            labels_per_image[negative_idxs] = 0.0

            # Присвоїти між мітками
            between_idxs = matched_idxs_per_image == self.proposal_matcher.BETWEEN_THRESHOLDS
            labels_per_image[between_idxs] = -1.0

            labels.append(labels_per_image)
            matched_gt_bboxs.append(matched_gt_bboxs_per_image)
        return torch.stack(labels, dim=0), torch.stack(matched_gt_bboxs, dim=0)
    
    # Обчислює втрати для класифікації та регресії обмежувальної рамки.
    def calculate_loss(self, class_logits, pred_bbox_deltas, labels, regression_targets):
        N, P, N_Cx4 = pred_bbox_deltas.shape
        pred_bbox_deltas = pred_bbox_deltas.view(N, P, N_Cx4 // 4, 4)
        
        sampled_positive_masks, sampled_negative_masks = self.sampler(labels)
        sampled_masks = sampled_positive_masks | sampled_negative_masks

        sampled_class_logits, sampled_labels = class_logits[sampled_masks], labels[sampled_masks]
        
        # Classification loss
        if self.loss_type in ['ce_smoothl1', 'ce_giou']:
            roi_cls_loss = F.cross_entropy(sampled_class_logits, sampled_labels)
        elif self.loss_type in ['focal_smoothl1', 'focal_ciou']:
            # Convert to one-hot and use focal loss
            targets_one_hot = torch.zeros_like(sampled_class_logits)
            targets_one_hot.scatter_(1, sampled_labels.unsqueeze(1), 1)
            roi_cls_loss = utils.focal_loss(sampled_class_logits, targets_one_hot)
        
        sampled_deltas, sampled_regression_targets = (pred_bbox_deltas[sampled_positive_masks], 
                                                      regression_targets[sampled_positive_masks])
        sampled_positive_labels = labels[sampled_positive_masks]
        sampled_regression = []
        for sampled_positive_label, sampled_delta in zip(sampled_positive_labels, sampled_deltas):
            sampled_regression.append(sampled_delta[sampled_positive_label])
        
        if len(sampled_regression) == 0:
            roi_loc_loss = None
        else:
            sampled_regression = torch.stack(sampled_regression, dim=0)
            if self.loss_type in ['ce_smoothl1', 'focal_smoothl1']:
                roi_loc_loss = F.smooth_l1_loss(sampled_regression, sampled_regression_targets)
            elif self.loss_type == 'ce_giou':
                # Decode boxes and compute GIoU loss
                proposals = self.box_coder.decode(sampled_regression, sampled_regression_targets)
                roi_loc_loss = utils.giou_loss(proposals, sampled_regression_targets).mean()
            elif self.loss_type == 'focal_ciou':
                # Decode boxes and compute CIoU loss
                proposals = self.box_coder.decode(sampled_regression, sampled_regression_targets)
                roi_loc_loss = utils.ciou_loss(proposals, sampled_regression_targets).mean()
        
        return roi_cls_loss, roi_loc_loss
    
    def convert(self, class_logits, pred_bbox_deltas, proposals):
        # Перетворює class_logits та pred_bbox_deltas на оцінки, мітки та виявлення.
        # Видаляє фоновий клас і декодує pred_bbox_deltas до обмежувальних рамок.
        # (N, P, num_classes), (N, P, num_classes * 4) -> (N, P_without_background), (N, P_without_background, 4)
        N, P, N_Cx4 = pred_bbox_deltas.shape
        pred_bbox_deltas = pred_bbox_deltas.view(N, P, N_Cx4 // 4, 4)
        probs = F.softmax(class_logits, dim=-1)
        
        pred_scores, pred_labels, pred_deltas, pred_proposals = [], [], [], []
        for probs_per_img, pred_bbox_deltas_per_img, proposals_per_img in zip(probs, pred_bbox_deltas, proposals):
            pred_scores_per_img, pred_labels_per_img = torch.max(probs_per_img[:,1:], dim=-1)
            pred_labels_per_img += 1
            label_map = torch.arange(self.num_classes, device=probs_per_img.device).expand_as(probs_per_img)
            mask = label_map == pred_labels_per_img[:, None]
            class_idx = pred_labels_per_img > 0
            
            pred_scores.append(pred_scores_per_img[class_idx])
            pred_labels.append(pred_labels_per_img[class_idx])
            pred_deltas.append(pred_bbox_deltas_per_img[mask][class_idx])
            pred_proposals.append(proposals_per_img[class_idx])
        
        pred_scores, pred_labels = torch.stack(pred_scores, dim=0), torch.stack(pred_labels, dim=0)
        pred_deltas, pred_proposals = torch.stack(pred_deltas, dim=0), torch.stack(pred_proposals, dim=0)
        detections = self.box_coder.decode(pred_deltas, pred_proposals)
        return pred_scores, pred_labels, detections
    
    # Фільтрує виявлення на основі розміру, порогового значення балів і застосовує немаксимальне придушення (NMS).
    def filter_detections(self, images, class_logits, pred_bbox_deltas, proposals):
        pred_scores, pred_labels, detections = self.convert(class_logits, pred_bbox_deltas, proposals)
        
        filtered_scores, filtered_labels, filtered_detections = [], [], []
        for img, scores_per_img, labels_per_img, detections_per_img in zip(images, pred_scores, pred_labels, detections):
            detections_per_img = torchvision.ops.clip_boxes_to_image(detections_per_img, tuple(img.shape[-2:]))
            
            keep_idx = torchvision.ops.remove_small_boxes(detections_per_img, self.min_size)
            scores_per_img, labels_per_img, detections_per_img = (scores_per_img[keep_idx], 
                                                                  labels_per_img[keep_idx], 
                                                                  detections_per_img[keep_idx])

            keep_idx = scores_per_img > self.score_thresh
            scores_per_img, labels_per_img, detections_per_img = (scores_per_img[keep_idx], 
                                                                  labels_per_img[keep_idx], 
                                                                  detections_per_img[keep_idx])
            # NMS
            keep_idx = torchvision.ops.batched_nms(detections_per_img, scores_per_img, labels_per_img, self.nms_thresh)
            scores_per_img, labels_per_img, detections_per_img = (scores_per_img[keep_idx], 
                                                                  labels_per_img[keep_idx], 
                                                                  detections_per_img[keep_idx])
            # відсортувати за балами та вибрати топ-n
            top_idx = torch.argsort(scores_per_img, descending=True)[:self.top_n]
            scores_per_img, labels_per_img, detections_per_img = (scores_per_img[top_idx], 
                                                                  labels_per_img[top_idx], 
                                                                  detections_per_img[top_idx])
            filtered_scores.append(scores_per_img)
            filtered_labels.append(labels_per_img)
            filtered_detections.append(detections_per_img)
            
        filtered_scores = torch.stack(filtered_scores, dim=0)
        filtered_labels = torch.stack(filtered_labels, dim=0)
        filtered_detections = torch.stack(filtered_detections, dim=0)
        return filtered_scores, filtered_labels, filtered_detections
    
    # Прямий хід моделі Fast R-CNN. 
    # Під час навчання обчислює втрати. 
    # Під час виведення - виконує детекцію.
    def forward(self, images, features, proposals, gt_labels=None, gt_bboxs=None):
        class_logits, pred_bbox_deltas = self.roi_head(images, features, proposals)
        if self.training:
            labels, matched_gt_bboxs = self.assign_gt_to_proposals(proposals, gt_labels, gt_bboxs)
            regression_targets = self.box_coder.encode(matched_gt_bboxs, proposals)
            roi_cls_loss, roi_loc_loss = self.calculate_loss(class_logits, pred_bbox_deltas, labels, regression_targets)
            return None, None, None, roi_cls_loss, roi_loc_loss
        else:
            pred_scores, pred_labels, pred_detections = self.filter_detections(images, 
                                                                               class_logits, pred_bbox_deltas, proposals)
            return pred_labels, pred_scores, pred_detections, None, None