"""The desktop icon uses the same pulse mark as the recording viewer."""
from PIL import Image, ImageDraw


def desktop_icon():
    icon = Image.new("RGBA", (256, 256))
    drawing = ImageDraw.Draw(icon)
    drawing.rounded_rectangle((0, 0, 255, 255), radius=52, fill="#087f8c")
    points = [(3, 18), (8, 18), (11, 9), (16, 26), (21, 5), (24, 18), (29, 18)]
    drawing.line([(x * 7 + 16, y * 7 + 16) for x, y in points], fill="white", width=14, joint="curve")
    return icon
