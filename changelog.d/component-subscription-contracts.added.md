- Stage ADR-034's private class-time component subscription validation and prevent
  subscription callbacks from also becoming event/API/RPC handlers or being
  directly invoked under permissive event-security policies. The interactive
  component API is not yet exported; concrete binding and dispatch remain pending.
