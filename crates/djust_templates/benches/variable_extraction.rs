use criterion::{criterion_group, criterion_main, BenchmarkId, Criterion};
use djust_templates::extract_template_variables;
use std::hint::black_box;

fn bench_simple_variables(c: &mut Criterion) {
    let mut group = c.benchmark_group("simple_variables");

    let templates = vec![
        ("single_var", "{{ name }}"),
        ("nested_1_level", "{{ user.email }}"),
        ("nested_2_levels", "{{ lease.property.name }}"),
        ("nested_3_levels", "{{ lease.property.owner.name }}"),
        ("nested_deep", "{{ a.b.c.d.e.f.g.h.i.j }}"),
    ];

    for (name, template) in templates {
        group.bench_with_input(BenchmarkId::from_parameter(name), &template, |b, &tmpl| {
            b.iter(|| extract_template_variables(black_box(tmpl)));
        });
    }

    group.finish();
}

fn bench_multiple_variables(c: &mut Criterion) {
    let mut group = c.benchmark_group("multiple_variables");

    let templates = vec![
        (
            "5_vars",
            "{{ a }} {{ b }} {{ c }} {{ d }} {{ e }}",
        ),
        (
            "10_vars",
            "{{ a }} {{ b }} {{ c }} {{ d }} {{ e }} {{ f }} {{ g }} {{ h }} {{ i }} {{ j }}",
        ),
        (
            "5_nested",
            "{{ user.name }} {{ user.email }} {{ user.profile.bio }} {{ user.profile.avatar }} {{ user.settings.theme }}",
        ),
    ];

    for (name, template) in templates {
        group.bench_with_input(BenchmarkId::from_parameter(name), &template, |b, &tmpl| {
            b.iter(|| extract_template_variables(black_box(tmpl)));
        });
    }

    group.finish();
}

fn bench_template_tags(c: &mut Criterion) {
    let mut group = c.benchmark_group("template_tags");

    let for_loop = r#"
        {% for item in items %}
            {{ item.name }}
            {{ item.description }}
        {% endfor %}
    "#;

    let if_condition = r#"
        {% if user.is_authenticated %}
            {{ user.profile.name }}
        {% else %}
            Guest
        {% endif %}
    "#;

    let nested_tags = r#"
        {% for category in categories %}
            {% if category.is_active %}
                {% for item in category.items %}
                    {{ item.name }}
                {% endfor %}
            {% endif %}
        {% endfor %}
    "#;

    group.bench_function("for_loop", |b| {
        b.iter(|| extract_template_variables(black_box(for_loop)));
    });

    group.bench_function("if_condition", |b| {
        b.iter(|| extract_template_variables(black_box(if_condition)));
    });

    group.bench_function("nested_tags", |b| {
        b.iter(|| extract_template_variables(black_box(nested_tags)));
    });

    group.finish();
}

fn bench_real_world_templates(c: &mut Criterion) {
    let mut group = c.benchmark_group("real_world_templates");

    let rental_dashboard = r#"
        <div class="dashboard">
            <h1>{{ site.name }}</h1>
            {% for lease in expiring_soon %}
                <div class="lease-card">
                    <h2>{{ lease.property.name }}</h2>
                    <p>{{ lease.property.address }}</p>
                    <p>Tenant: {{ lease.tenant.user.get_full_name }}</p>
                    <p>Email: {{ lease.tenant.user.email }}</p>
                    <p>Phone: {{ lease.tenant.phone }}</p>
                    <p>Expires: {{ lease.end_date|date:"M d, Y" }}</p>
                    {% if lease.property.maintenance_requests.count > 0 %}
                        <span class="badge">{{ lease.property.maintenance_requests.count }} pending</span>
                    {% endif %}
                </div>
            {% endfor %}
        </div>
    "#;

    let e_commerce = r#"
        <div class="products">
            {% for category in categories.active %}
                <div class="category">
                    <h2>{{ category.name }}</h2>
                    <p>{{ category.description }}</p>
                    {% for product in category.products.available %}
                        <div class="product">
                            <img src="{{ product.image.url }}" alt="{{ product.name }}">
                            <h3>{{ product.name }}</h3>
                            <p>{{ product.description|truncatewords:20 }}</p>
                            <span class="price">${{ product.price }}</span>
                            {% if product.reviews.exists %}
                                <div class="rating">
                                    {{ product.reviews.average_rating }}/5
                                    ({{ product.reviews.count }} reviews)
                                </div>
                            {% endif %}
                            {% if product.is_on_sale %}
                                <span class="sale">{{ product.discount_percentage }}% OFF</span>
                            {% endif %}
                        </div>
                    {% endfor %}
                </div>
            {% endfor %}
        </div>
    "#;

    group.bench_function("rental_dashboard", |b| {
        b.iter(|| extract_template_variables(black_box(rental_dashboard)));
    });

    group.bench_function("e_commerce", |b| {
        b.iter(|| extract_template_variables(black_box(e_commerce)));
    });

    group.finish();
}

fn bench_large_templates(c: &mut Criterion) {
    let mut group = c.benchmark_group("large_templates");

    // Generate templates with varying sizes
    for size in [10, 50, 100, 200].iter() {
        let mut template_parts = Vec::new();
        for i in 0..*size {
            template_parts.push(format!(
                r#"
                {{% for obj{i} in list{i} %}}
                    {{{{ obj{i}.field1 }}}}
                    {{{{ obj{i}.field2 }}}}
                    {{{{ obj{i}.nested.value }}}}
                {{% endfor %}}
            "#
            ));
        }
        let template = template_parts.join("\n");

        group.bench_with_input(
            BenchmarkId::from_parameter(format!("{size}_iterations")),
            &template,
            |b, tmpl| {
                b.iter(|| extract_template_variables(black_box(tmpl)));
            },
        );
    }

    group.finish();
}

fn bench_deduplication(c: &mut Criterion) {
    let mut group = c.benchmark_group("deduplication");

    let many_duplicates = r#"
        {{ lease.property.name }}
        {{ lease.property.name }}
        {{ lease.property.name }}
        {{ lease.property.address }}
        {{ lease.property.address }}
        {{ lease.tenant.email }}
        {{ lease.tenant.email }}
        {{ lease.tenant.email }}
    "#;

    let no_duplicates = r#"
        {{ lease.property.name }}
        {{ lease.property.address }}
        {{ lease.property.city }}
        {{ lease.property.state }}
        {{ lease.property.zip }}
        {{ lease.tenant.email }}
        {{ lease.tenant.phone }}
        {{ lease.tenant.name }}
    "#;

    group.bench_function("many_duplicates", |b| {
        b.iter(|| extract_template_variables(black_box(many_duplicates)));
    });

    group.bench_function("no_duplicates", |b| {
        b.iter(|| extract_template_variables(black_box(no_duplicates)));
    });

    group.finish();
}

criterion_group!(
    benches,
    bench_simple_variables,
    bench_multiple_variables,
    bench_template_tags,
    bench_real_world_templates,
    bench_large_templates,
    bench_deduplication,
);
criterion_main!(benches);
