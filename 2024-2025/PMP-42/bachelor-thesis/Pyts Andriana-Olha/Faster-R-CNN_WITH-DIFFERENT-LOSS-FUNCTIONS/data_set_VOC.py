import torch, torchvision


class VOC_Detection(torch.utils.data.Dataset):
    # Ініціалізує набір даних VOC_Detection.
    def __init__(self, root, year, image_set, download, transforms, use_diff):
        self.dataset = torchvision.datasets.VOCDetection(root, year, image_set, download)
        self.transforms = transforms
        self.use_diff = use_diff
        self.VOC_LABELS = ('__background__', 
                           'aeroplane', 'bicycle', 'bird', 'boat', 'bottle', 'bus', 'car', 'cat', 'chair', 'cow', 
                           'diningtable', 'dog', 'horse','motorbike', 'person', 'pottedplant', 'sheep', 'sofa', 
                           'train', 'tvmonitor')
        
    def __getitem__(self, idx):
        # Отримує зображення та ціль для заданого індексу.
        img, target = self.dataset[idx]
        labels, bboxs = [], []
        for info in target['annotation']['object']:
            if self.use_diff or (int(info['difficult']) == 0):
                labels.append(self.VOC_LABELS.index(info['name']))
                # Робить піксельні індекси заснованими на 0
                bboxs.append(torch.FloatTensor([float(info['bndbox']['xmin'])-1, float(info['bndbox']['ymin'])-1, 
                                                float(info['bndbox']['xmax'])-1, float(info['bndbox']['ymax'])-1]))
        
        labels, bboxs = torch.tensor(labels, dtype=int), torch.stack(bboxs, dim=0)
        if self.transforms: img, bboxs = self.transforms(img, bboxs)
        return img, labels, bboxs

    def __len__(self):
        # Повертає довжину набору даних.
        return len(self.dataset)
    
