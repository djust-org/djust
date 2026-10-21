- **Hot view replacement follows an edit to a mixin or base class in another
  module.** Saving a module that defines no LiveView (a mixin) did nothing,
  and views in other modules kept inheriting from the class objects they were
  built with. The saved module and every module defining a LiveView whose MRO
  holds one of its classes are now reloaded, bases before subclasses, so a
  fresh page load, a new socket mount and a mounted view (`hvr_meta.modules`)
  run the new code. A reload also drops the module's cached bytecode, which two
  same-size saves within one second would otherwise reuse. Cases in `python/djust/tests/test_hvr_cross_module.py`.
