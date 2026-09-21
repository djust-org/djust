- **Overlapping HTTP events:** Completing or failing one HTTP fallback event no
  longer clears loading for another request on the same control. Cache-hit
  operations use the same ownership tracking and release their state in finally.
