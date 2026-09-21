- **Legacy async completion:** Tokenless async_pending acknowledgements now
  retain their originating control scope until the matching async event result
  arrives, without releasing modern tokenized batches or newer requests.
