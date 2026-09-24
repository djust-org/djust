- **Private component observation retries:** consume visibility report sequences
  atomically in the state backend before callbacks, without saving failed
  application session mutations. Memory supports one process; Redis shares
  claims across workers. Missing or expired cursors fail closed until rebinding.
  Browser observation wiring and recovery remain staged ADR-034 work.
