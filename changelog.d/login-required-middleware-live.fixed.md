- **Live transports follow Django's `LoginRequiredMiddleware`.** With
  `django.contrib.auth.middleware.LoginRequiredMiddleware` (Django 5.1+, or a
  subclass) in `MIDDLEWARE`, a view mounted over a live transport (WebSocket,
  SSE, API dispatch, `mount_batch`) now gets the answer the middleware gives
  for its page: an anonymous user is sent to the login page, where the
  middleware would send them (its `login_url` and `redirect_field_name` for
  that route), unless the view is public. A view is public when its
  `dispatch` carries Django's `login_not_required`
  (`@method_decorator(login_not_required, name="dispatch")`, public
  everywhere), or when the URLconf routes that view class through
  `login_not_required(MyView.as_view())`, or when the request's own route
  serves the view and the installed middleware (subclass exemptions
  included) lets it through. A view the URLconf does not route, mounted
  beside the page view or hydrated as a lazy child over the WebSocket, needs
  the `dispatch` marker to be mounted anonymously; naming a public page's URL
  does not make it public. When the middleware refuses such a view, only that
  view is refused (an `error` frame with `code: "login_required"`, or a
  `failed[]` entry in the `mount_batch` reply) and the page stays where it is;
  the first view mounted on a socket is the page view, and its refusal sends
  the page to the login page. That is the middleware's answer: a view refused
  by its own `login_required = True` still sends the page to the login page,
  and a view that relies only on `permission_required`, `check_permissions()`
  or Django's auth mixins gets the middleware's answer first, so beside the
  page view it is refused on its own. On SSE and the API the request is the
  endpoint's, so a view served there anonymously needs the `dispatch` marker
  or a `login_not_required` route of its own (and the endpoint itself must be
  exempt from the middleware for the request to arrive). Views rendered inside
  a page (`{% live_render %}` children) take their page's answer, as Django
  never checks what a page includes, and a page's own HTTP GET and POST are
  left to the middleware itself. djust's own `login_required = False` is not
  an exemption, as it is not for the middleware. Without the middleware, or on
  Django < 5.1, nothing changes.
