class ProcessingError(Exception):
    """Базовая ошибка обработки сообщения из очереди"""


class RetryableProcessingError(ProcessingError):
    """Временная ошибка: сообщение следует обработать повторно"""


class PermanentProcessingError(ProcessingError):
    """Постоянная ошибка: повтор не изменит результат обработки"""


class PaymentNotFoundError(PermanentProcessingError):
    """Платёж не существует"""

    pass


class IdempotencyConflictError(Exception):
    """Ключ идемпотентности повторно использован с другим запросом"""

    pass
