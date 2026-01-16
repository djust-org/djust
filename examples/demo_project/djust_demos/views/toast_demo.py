"""
Toast component demonstration view.
"""
from django.shortcuts import render


def toast_demo(request):
    """Demonstrate Toast component with various configurations"""
    from djust.components.ui import Toast

    # Import Rust version if available
    try:
        from djust._rust import RustToast
        use_rust = True
        ToastClass = RustToast
    except ImportError:
        use_rust = False
        ToastClass = Toast

    context = {
        'success_toast': ToastClass(
            title="Success",
            message="Your changes have been saved successfully!",
            variant="success",
            dismissable=True,
            show_icon=True
        ),
        'info_toast': ToastClass(
            title="Information",
            message="New updates are available for download.",
            variant="info",
            dismissable=True,
            show_icon=True
        ),
        'warning_toast': ToastClass(
            title="Warning",
            message="Please review your input before submitting.",
            variant="warning",
            dismissable=True,
            show_icon=True
        ),
        'danger_toast': ToastClass(
            title="Error",
            message="An error occurred while processing your request.",
            variant="danger",
            dismissable=True,
            show_icon=True
        ),
        'no_close_toast': ToastClass(
            title="System Message",
            message="This toast cannot be dismissed by the user.",
            variant="info",
            dismissable=False,
            show_icon=True
        ),
        'no_icon_toast': ToastClass(
            title="Plain Toast",
            message="This toast has no icon.",
            variant="secondary",
            dismissable=True,
            show_icon=False
        ),
        'message_only_toast': ToastClass(
            title="",
            message="This is a simple message without a title.",
            variant="info",
            dismissable=True,
            show_icon=True
        ),
        'use_rust': use_rust,
    }

    return render(request, 'demos/toast_demo.html', context)
