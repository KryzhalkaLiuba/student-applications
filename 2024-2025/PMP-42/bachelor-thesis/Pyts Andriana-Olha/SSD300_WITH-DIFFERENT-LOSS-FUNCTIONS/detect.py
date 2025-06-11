from torchvision import transforms
from torchvision.utils import save_image
from utils import *
from PIL import Image, ImageDraw, ImageFont

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Контрольна точка моделі завантаження
checkpoint = 'checkpoint_ssd300.pth.tar'
checkpoint = torch.load(checkpoint)
start_epoch = checkpoint['epoch'] + 1
print('\nLoaded checkpoint from epoch %d.\n' % start_epoch)
model = checkpoint['model']
model = model.to(device)
model.eval()

# Трансформує
resize = transforms.Resize((300, 300))
to_tensor = transforms.ToTensor()
normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                 std=[0.229, 0.224, 0.225])


def detect(original_image, min_score, max_overlap, top_k, suppress=None):
    """
    Виявляйте об’єкти на зображенні за допомогою навченого SSD300 і візуалізуйте результати.

    :param original_image: зображення, зображення PIL
    :param min_score: мінімальний поріг для того, щоб виявлений ящик вважався відповідним для певного класу
    :param max_overlap: максимальне перекриття може мати два блоки, щоб той із нижчим балом не пригнічувався за допомогою немаксимального придушення (NMS)
    :param top_k: якщо є багато результуючих виявлень у всіх класах, збережіть лише верхній 'k'
    :param suppress: класи, які, як ви точно знаєте, не можуть бути на зображенні або вам не потрібні на зображенні, список
    :return: анотоване зображення, зображення PIL
    """

    # Трансформувати
    image = normalize(to_tensor(resize(original_image)))

    # Перейти до пристрою за умовчанням
    image = image.to(device)

    # Передній проп.
    predicted_locs, predicted_scores = model(image.unsqueeze(0))

    # Виявлення об'єктів у вихідних даних SSD
    det_boxes, det_labels, det_scores = model.detect_objects(predicted_locs, predicted_scores, min_score=min_score,
                                                             max_overlap=max_overlap, top_k=top_k)

    # Перемістіть виявлення до ЦП
    det_boxes = det_boxes[0].to('cpu')

    # Перетворення до вихідних розмірів зображення
    original_dims = torch.FloatTensor(
        [original_image.width, original_image.height, original_image.width, original_image.height]).unsqueeze(0)
    det_boxes = det_boxes * original_dims

    # Декодувати цілочисельні мітки класу
    det_labels = [rev_label_map[l] for l in det_labels[0].to('cpu').tolist()]

    # Якщо об’єкти не знайдено, для виявлених міток буде встановлено ['0.'], тобто ['background'] у SSD300.detect_objects() у model.py
    if det_labels == ['background']:
        # Просто поверніть оригінальне зображення
        return original_image

    # Анотуйте
    annotated_image = original_image
    draw = ImageDraw.Draw(annotated_image)
    font = ImageFont.load_default()
    #font = ImageFont.truetype("./arial.ttf", 15)

    # При необхідності придушити певні класи
    for i in range(det_boxes.size(0)):
        if suppress is not None:
            if det_labels[i] in suppress:
                continue

        # ящики
        box_location = det_boxes[i].tolist()
        draw.rectangle(xy=box_location, outline=label_color_map[det_labels[i]])
        draw.rectangle(xy=[l + 1. for l in box_location], outline=label_color_map[
            det_labels[i]])  # a second rectangle at an offset of 1 pixel to increase line thickness
        # draw.rectangle(xy=[l + 2. for l in box_location], outline=label_color_map[
        #     det_labels[i]])  # a third rectangle at an offset of 1 pixel to increase line thickness
        # draw.rectangle(xy=[l + 3. for l in box_location], outline=label_color_map[
        #     det_labels[i]])  # a fourth rectangle at an offset of 1 pixel to increase line thickness

        # Text
        text_size = font.getsize(det_labels[i].upper())
        text_location = [box_location[0] + 2., box_location[1] - text_size[1]]
        textbox_location = [box_location[0], box_location[1] - text_size[1], box_location[0] + text_size[0] + 4.,
                            box_location[1]]
        draw.rectangle(xy=textbox_location, fill=label_color_map[det_labels[i]])
        draw.text(xy=text_location, text=det_labels[i].upper(), fill='white',
                  font=font)
    del draw

    return annotated_image


if __name__ == '__main__':
    img_path = '/mnt2/datasets/VOCdevkit/VOC2007/JPEGImages/000123.jpg'
    original_image = Image.open(img_path, mode='r')
    original_image = original_image.convert('RGB')
    annotated_image = detect(original_image, min_score=0.2, max_overlap=0.5, top_k=200)
    annotated_image.save("./result.jpg")