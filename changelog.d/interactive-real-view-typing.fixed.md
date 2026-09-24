- **Real-view component typing** — follow Django source declarations in the
  isolated typing proof and declare LiveView's actual constructor in its stub.
  Both supported checkers now reject all twenty negative component/output cases
  on real framework classes, without a new dependency or a substitute owner.
