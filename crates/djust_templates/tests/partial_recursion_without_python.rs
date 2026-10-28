//! Recursive templates must be safe for Rust callers without initializing Python.

use djust_core::Context;
use djust_templates::{parser, Template};

fn in_isolated_subprocess(test_name: &str) -> bool {
    use std::process::{Command, Stdio};
    use std::time::{Duration, Instant};
    const CHILD: &str = "DJUST_TEST_DEEP_PARTIAL_CHILD";
    if std::env::var(CHILD).as_deref() == Ok(test_name) {
        return true;
    }
    let mut child = Command::new(std::env::current_exe().unwrap())
        .args(["--exact", test_name, "--nocapture"])
        .env(CHILD, test_name)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(60);
    loop {
        if child.try_wait().unwrap().is_some() {
            break;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            panic!("deep partial subprocess exceeded its 60 second deadline");
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    let output = child.wait_with_output().unwrap();
    assert!(
        output.status.success(),
        "child failed: {}; stdout: {}; stderr: {}",
        output.status,
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    false
}

#[test]
fn recursion_error_can_be_inspected_without_python() {
    if !in_isolated_subprocess("recursion_error_can_be_inspected_without_python") {
        return;
    }
    assert!(pyo3::Python::try_attach(|_| ()).is_none());
    parser::set_django_partials(true);
    let template =
        Template::new("{% partialdef p %}{% partial p %}{% endpartialdef %}{% partial p %}")
            .unwrap();
    let error = template.render(&Context::new()).unwrap_err();
    assert!(error.to_string().to_lowercase().contains("recursion"));
    assert!(pyo3::Python::try_attach(|_| ()).is_none());
}

#[test]
fn concurrent_recursive_partials_are_bounded_on_small_rust_thread_stacks() {
    if !in_isolated_subprocess(
        "concurrent_recursive_partials_are_bounded_on_small_rust_thread_stacks",
    ) {
        return;
    }
    use std::sync::{Arc, Barrier};
    parser::set_django_partials(true);
    let template = Arc::new(
        Template::new("{% partialdef p %}{% partial p %}{% endpartialdef %}{% partial p %}")
            .unwrap(),
    );
    let barrier = Arc::new(Barrier::new(4));
    let workers: Vec<_> = (0..4)
        .map(|_| {
            let template = template.clone();
            let barrier = barrier.clone();
            std::thread::Builder::new()
                .stack_size(512 * 1024)
                .spawn(move || {
                    barrier.wait();
                    let error = template.render(&Context::new()).unwrap_err();
                    assert!(error.to_string().contains("recursion"));
                })
                .unwrap()
        })
        .collect();
    for worker in workers {
        worker.join().unwrap();
    }
}

#[test]
fn finite_deep_partial_renders_without_python() {
    if !in_isolated_subprocess("finite_deep_partial_renders_without_python") {
        return;
    }
    use djust_core::Value;
    parser::set_django_partials(true);
    for stack_size in [512 * 1024, 2 * 1024 * 1024, 8 * 1024 * 1024] {
        std::thread::Builder::new().stack_size(stack_size).spawn(|| {
            let mut node = Value::None;
            for _ in 0..300 {
                node = Value::Object([("child".into(), node)].into_iter().collect());
            }
            let mut context = Context::new();
            context.set("node".to_owned(), node);
            context.set_emit_dj_if_markers(false);
            let template = Template::new(
                "{% partialdef p %}X{% if node.child %}{% with node=node.child %}{% partial p %}{% endwith %}{% endif %}{% endpartialdef %}{% partial p %}",
            ).unwrap();
            assert_eq!(template.render(&context).unwrap(), "X".repeat(300));

            struct PanickingLoader;
            impl djust_templates::inheritance::TemplateLoader for PanickingLoader {
                fn shared_handle(&self) -> std::sync::Arc<dyn djust_templates::inheritance::TemplateLoader + Send + Sync> {
                    std::sync::Arc::new(PanickingLoader)
                }
                fn load_template(&self, _name: &str) -> djust_core::Result<Vec<djust_templates::parser::Node>> {
                    panic!("panic deep in partial")
                }
            }
            let throwing = Template::new(
                "{% partialdef p %}{% if node.child %}{% with node=node.child %}{% partial p %}{% endwith %}{% else %}{% include 'panic.html' %}{% endif %}{% endpartialdef %}{% partial p %}",
            ).unwrap();
            assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                throwing.render_with_loader(&context, &PanickingLoader)
            })).is_err());
            assert_eq!(template.render(&context).unwrap(), "X".repeat(300));
            assert!(pyo3::Python::try_attach(|_| ()).is_none());
        }).unwrap().join().unwrap();
    }
}
