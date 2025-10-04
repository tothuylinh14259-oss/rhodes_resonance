class ToolResponse:
    def __init__(self, content=None, metadata=None):
        self.content = list(content or [])
        self.metadata = dict(metadata or {})

