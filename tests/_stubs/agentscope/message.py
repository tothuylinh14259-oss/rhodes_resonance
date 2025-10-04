class TextBlock(dict):
    def __init__(self, type: str = "text", text: str = ""):
        super().__init__()
        self["type"] = type
        self["text"] = text

