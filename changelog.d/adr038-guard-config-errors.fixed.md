- **Exposure configuration errors stay visible behind the protected HTTP
  entry.** The ADR-038 constructor guard now raises `ExposureConfigurationError`,
  a subclass of `ImproperlyConfigured`. The protected HTTP entry for nonlegacy
  views turns application failures into a generic 500, but lets this error
  through, because its messages are framework-authored and carry no view
  values. Under DEBUG, a view with an invalid `exposure_policy` still says why.
