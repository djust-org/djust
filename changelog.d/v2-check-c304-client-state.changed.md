- System check `djust.C304` now reads declared client state: it warns about a
  `state(..., client=True)` field, including a `persist="client"` one, whose
  name looks like a credential or personal data. It used to read the public
  attributes of views that set the removed `enable_state_snapshot`.
