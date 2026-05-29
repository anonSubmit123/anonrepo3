class InvalidStateException(RuntimeError):

    def __init__(self, message="Object is in an invalid state."):
        self.message = message
        super().__init__(self.message)