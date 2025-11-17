"""
Management command to generate realistic sample data for rental property management.

Usage:
    python manage.py generate_rental_data
    python manage.py generate_rental_data --clear  # Clear existing data first
"""

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import datetime, timedelta, date
from decimal import Decimal
import random

from djust_rentals.models import Property, Tenant, Lease, MaintenanceRequest, Payment, Expense


class Command(BaseCommand):
    help = 'Generate realistic sample data for rental property management'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing rental data before generating new data',
        )

    def handle(self, *args, **options):
        if options['clear']:
            self.stdout.write(self.style.WARNING('Clearing existing rental data...'))
            MaintenanceRequest.objects.all().delete()
            Payment.objects.all().delete()
            Expense.objects.all().delete()
            Lease.objects.all().delete()
            Tenant.objects.all().delete()
            Property.objects.all().delete()
            # Delete tenant users
            User.objects.filter(username__startswith='tenant').delete()
            self.stdout.write(self.style.SUCCESS('Existing data cleared'))

        self.stdout.write('Generating sample rental data...')

        # Generate properties
        properties = self._generate_properties()
        self.stdout.write(self.style.SUCCESS(f'Created {len(properties)} properties'))

        # Generate tenants
        tenants = self._generate_tenants()
        self.stdout.write(self.style.SUCCESS(f'Created {len(tenants)} tenants'))

        # Generate leases
        leases = self._generate_leases(properties, tenants)
        self.stdout.write(self.style.SUCCESS(f'Created {len(leases)} leases'))

        # Generate payments
        payments = self._generate_payments(leases)
        self.stdout.write(self.style.SUCCESS(f'Created {len(payments)} payments'))

        # Generate maintenance requests
        maintenance = self._generate_maintenance(properties, tenants)
        self.stdout.write(self.style.SUCCESS(f'Created {len(maintenance)} maintenance requests'))

        # Generate expenses
        expenses = self._generate_expenses(properties)
        self.stdout.write(self.style.SUCCESS(f'Created {len(expenses)} expenses'))

        self.stdout.write(self.style.SUCCESS('\nSample data generation complete!'))
        self.stdout.write(f'Total records created:')
        self.stdout.write(f'  - Properties: {len(properties)}')
        self.stdout.write(f'  - Tenants: {len(tenants)}')
        self.stdout.write(f'  - Leases: {len(leases)}')
        self.stdout.write(f'  - Payments: {len(payments)}')
        self.stdout.write(f'  - Maintenance Requests: {len(maintenance)}')
        self.stdout.write(f'  - Expenses: {len(expenses)}')

    def _generate_properties(self):
        """Generate 15 realistic properties"""
        properties_data = [
            # Apartments
            {'name': 'Sunset Apartments #101', 'type': 'apartment', 'bedrooms': 2, 'bathrooms': 1.0, 'sqft': 850, 'rent': 1850, 'city': 'San Francisco'},
            {'name': 'Sunset Apartments #205', 'type': 'apartment', 'bedrooms': 1, 'bathrooms': 1.0, 'sqft': 650, 'rent': 1500, 'city': 'San Francisco'},
            {'name': 'Parkview Apartments #3B', 'type': 'apartment', 'bedrooms': 2, 'bathrooms': 2.0, 'sqft': 950, 'rent': 2100, 'city': 'Oakland'},
            {'name': 'Downtown Loft #12', 'type': 'apartment', 'bedrooms': 1, 'bathrooms': 1.0, 'sqft': 700, 'rent': 1750, 'city': 'Berkeley'},
            {'name': 'Marina View #401', 'type': 'apartment', 'bedrooms': 3, 'bathrooms': 2.0, 'sqft': 1200, 'rent': 2800, 'city': 'San Francisco'},

            # Houses
            {'name': '123 Oak Street', 'type': 'house', 'bedrooms': 3, 'bathrooms': 2.0, 'sqft': 1500, 'rent': 3200, 'city': 'Palo Alto'},
            {'name': '456 Maple Avenue', 'type': 'house', 'bedrooms': 4, 'bathrooms': 2.5, 'sqft': 2000, 'rent': 4500, 'city': 'Mountain View'},
            {'name': '789 Pine Road', 'type': 'house', 'bedrooms': 3, 'bathrooms': 2.0, 'sqft': 1600, 'rent': 3500, 'city': 'Sunnyvale'},

            # Condos
            {'name': 'Bay Breeze Condo #5A', 'type': 'condo', 'bedrooms': 2, 'bathrooms': 2.0, 'sqft': 1100, 'rent': 2400, 'city': 'San Jose'},
            {'name': 'Hillside Condos #2C', 'type': 'condo', 'bedrooms': 2, 'bathrooms': 1.5, 'sqft': 950, 'rent': 2000, 'city': 'Fremont'},

            # Townhouses
            {'name': 'Willow Creek Townhouse #8', 'type': 'townhouse', 'bedrooms': 3, 'bathrooms': 2.5, 'sqft': 1400, 'rent': 2900, 'city': 'Redwood City'},
            {'name': 'Cedar Grove Townhouse #12', 'type': 'townhouse', 'bedrooms': 3, 'bathrooms': 2.0, 'sqft': 1300, 'rent': 2700, 'city': 'San Mateo'},

            # Studios
            {'name': 'Urban Studios #7', 'type': 'studio', 'bedrooms': 0, 'bathrooms': 1.0, 'sqft': 450, 'rent': 1400, 'city': 'San Francisco'},

            # Duplex
            {'name': '321 Elm Street Unit A', 'type': 'duplex', 'bedrooms': 2, 'bathrooms': 1.5, 'sqft': 1000, 'rent': 2300, 'city': 'Oakland'},
            {'name': '321 Elm Street Unit B', 'type': 'duplex', 'bedrooms': 2, 'bathrooms': 1.5, 'sqft': 1000, 'rent': 2300, 'city': 'Oakland'},
        ]

        properties = []
        streets = ['Main St', 'Broadway', 'Market St', 'University Ave', 'El Camino Real']

        for i, prop_data in enumerate(properties_data):
            # Generate address
            if 'Street' not in prop_data['name'] and 'Avenue' not in prop_data['name'] and 'Road' not in prop_data['name']:
                street_num = random.randint(100, 9999)
                street = random.choice(streets)
                address = f"{street_num} {street}, {prop_data['city']}, CA"
            else:
                address = f"{prop_data['name']}, {prop_data['city']}, CA"

            # Amenities based on property type
            amenities = []
            if prop_data['type'] in ['house', 'townhouse']:
                amenities.extend(['Backyard', 'Garage', 'Dishwasher', 'Washer/Dryer'])
            elif prop_data['type'] == 'apartment':
                amenities.extend(['Dishwasher', 'Pool', 'Fitness Center'])
            if prop_data['sqft'] > 1000:
                amenities.append('Walk-in Closet')
            if random.choice([True, False]):
                amenities.append('Hardwood Floors')

            # Determine status - most occupied, some available
            if i < 12:  # 12 out of 15 occupied
                status = 'occupied'
            elif i == 12:
                status = 'maintenance'
            else:
                status = 'available'

            property_obj = Property.objects.create(
                name=prop_data['name'],
                address=address,
                city=prop_data['city'],
                state='CA',
                zip_code=f'{94000 + i:05d}',
                property_type=prop_data['type'],
                bedrooms=prop_data['bedrooms'],
                bathrooms=Decimal(str(prop_data['bathrooms'])),
                square_feet=prop_data['sqft'],
                monthly_rent=Decimal(str(prop_data['rent'])),
                security_deposit=Decimal(str(prop_data['rent'])),  # One month rent
                status=status,
                description=f"Charming {prop_data['bedrooms']}-bedroom {prop_data['type']} in {prop_data['city']}. "
                           f"Features {prop_data['sqft']} sq ft of living space.",
                amenities=', '.join(amenities),
                parking_spaces=1 if prop_data['type'] in ['house', 'townhouse'] else 0,
                pet_friendly=random.choice([True, False]),
                furnished=False,
            )
            properties.append(property_obj)

        return properties

    def _generate_tenants(self):
        """Generate 12 tenant profiles"""
        tenant_names = [
            ('John', 'Smith'),
            ('Emily', 'Johnson'),
            ('Michael', 'Williams'),
            ('Sarah', 'Brown'),
            ('David', 'Jones'),
            ('Jessica', 'Garcia'),
            ('James', 'Miller'),
            ('Jennifer', 'Davis'),
            ('Robert', 'Rodriguez'),
            ('Maria', 'Martinez'),
            ('William', 'Hernandez'),
            ('Lisa', 'Lopez'),
        ]

        tenants = []
        for i, (first_name, last_name) in enumerate(tenant_names):
            # Create user account
            username = f"tenant{i+1}"
            email = f"{first_name.lower()}.{last_name.lower()}@example.com"

            user = User.objects.create_user(
                username=username,
                email=email,
                first_name=first_name,
                last_name=last_name,
                password='demo123',  # Demo password
            )

            # Create tenant profile
            tenant = Tenant.objects.create(
                user=user,
                phone=f'(555) {random.randint(100, 999)}-{random.randint(1000, 9999)}',
                emergency_contact_name=f"{random.choice(['Tom', 'Jane', 'Bob', 'Alice'])} {last_name}",
                emergency_contact_phone=f'(555) {random.randint(100, 999)}-{random.randint(1000, 9999)}',
                move_in_date=date.today() - timedelta(days=random.randint(30, 730)),
                employer=random.choice(['Google', 'Apple', 'Meta', 'Netflix', 'Salesforce', 'Uber', 'Airbnb']),
                monthly_income=Decimal(str(random.randint(60, 150) * 1000)),
                notes=f"Reliable tenant, always pays on time." if random.choice([True, False]) else "",
            )
            tenants.append(tenant)

        return tenants

    def _generate_leases(self, properties, tenants):
        """Generate leases - 10 active, 2 upcoming, 3 expired"""
        leases = []

        # Active leases (10) - first 10 occupied properties with first 10 tenants
        occupied_properties = [p for p in properties if p.status == 'occupied'][:10]
        for i, (property_obj, tenant) in enumerate(zip(occupied_properties, tenants[:10])):
            # Lease started 3-18 months ago
            months_ago = random.randint(3, 18)
            start_date = date.today() - timedelta(days=months_ago * 30)

            # 12-month lease
            end_date = start_date + timedelta(days=365)

            lease = Lease.objects.create(
                property=property_obj,
                tenant=tenant,
                start_date=start_date,
                end_date=end_date,
                monthly_rent=property_obj.monthly_rent,
                security_deposit=property_obj.security_deposit,
                rent_due_day=1,
                late_fee=Decimal('50.00'),
                status='active',
                terms="Standard residential lease agreement. No smoking. Pets allowed with deposit.",
            )
            leases.append(lease)

        # Upcoming leases (2) - next 2 tenants
        available_properties = [p for p in properties if p.status == 'available'][:2]
        for property_obj, tenant in zip(available_properties, tenants[10:12]):
            # Lease starts in 1-2 months
            start_date = date.today() + timedelta(days=random.randint(30, 60))
            end_date = start_date + timedelta(days=365)

            lease = Lease.objects.create(
                property=property_obj,
                tenant=tenant,
                start_date=start_date,
                end_date=end_date,
                monthly_rent=property_obj.monthly_rent,
                security_deposit=property_obj.security_deposit,
                rent_due_day=1,
                late_fee=Decimal('50.00'),
                status='upcoming',
                terms="Standard residential lease agreement.",
            )
            leases.append(lease)

        # Expired/terminated leases (3) - for variety in data
        for i in range(3):
            property_obj = random.choice(properties)
            tenant = random.choice(tenants)

            # Lease ended 1-6 months ago
            end_date = date.today() - timedelta(days=random.randint(30, 180))
            start_date = end_date - timedelta(days=365)

            lease = Lease.objects.create(
                property=property_obj,
                tenant=tenant,
                start_date=start_date,
                end_date=end_date,
                monthly_rent=property_obj.monthly_rent,
                security_deposit=property_obj.security_deposit,
                rent_due_day=1,
                late_fee=Decimal('50.00'),
                status='expired',
                terms="Previous lease agreement.",
            )
            leases.append(lease)

        return leases

    def _generate_payments(self, leases):
        """Generate payment history for active leases"""
        payments = []

        for lease in leases:
            if lease.status != 'active':
                continue

            # Generate payments from lease start until today
            current_date = lease.start_date
            today = date.today()

            while current_date <= today:
                # Due date is rent_due_day of the month
                payment_date = current_date.replace(day=lease.rent_due_day)

                # 80% pay on time, 15% pay late, 5% haven't paid yet
                payment_status_rand = random.random()
                if payment_status_rand < 0.80:
                    # On time payment
                    actual_payment_date = payment_date
                    status = 'completed'
                elif payment_status_rand < 0.95:
                    # Late payment (5-15 days late)
                    actual_payment_date = payment_date + timedelta(days=random.randint(5, 15))
                    status = 'completed'
                else:
                    # Pending payment
                    actual_payment_date = payment_date
                    status = 'pending'

                # Only create if payment date is in the past or very recent
                if actual_payment_date <= today + timedelta(days=2):
                    payment = Payment.objects.create(
                        lease=lease,
                        amount=lease.monthly_rent,
                        payment_date=actual_payment_date,
                        payment_method=random.choice(['bank_transfer', 'check', 'online', 'credit_card']),
                        status=status,
                        notes="Monthly rent payment" if random.random() < 0.8 else "",
                        transaction_id=f"TXN{random.randint(100000, 999999)}" if status == 'completed' else "",
                    )
                    payments.append(payment)

                # Move to next month
                next_month = (current_date.month % 12) + 1
                next_year = current_date.year + (1 if next_month == 1 else 0)
                current_date = current_date.replace(month=next_month, year=next_year)

        return payments

    def _generate_maintenance(self, properties, tenants):
        """Generate 20 maintenance requests"""
        requests = []

        maintenance_issues = [
            ('Leaky faucet in bathroom', 'The bathroom sink is dripping constantly', 'low'),
            ('AC not cooling', 'Air conditioning unit is running but not cooling the apartment', 'high'),
            ('Broken dishwasher', 'Dishwasher not starting, may need repair or replacement', 'medium'),
            ('Clogged toilet', 'Toilet is clogged and not flushing properly', 'urgent'),
            ('Broken window lock', 'Bedroom window lock is broken, security concern', 'high'),
            ('Smoke detector beeping', 'Smoke detector beeping intermittently, may need new battery', 'medium'),
            ('Water heater issue', 'No hot water, water heater may be malfunctioning', 'urgent'),
            ('Refrigerator not cold', 'Refrigerator not maintaining temperature, food spoiling', 'high'),
            ('Garbage disposal jammed', 'Kitchen garbage disposal is jammed and not working', 'low'),
            ('Squeaky door', 'Bedroom door squeaks loudly when opening/closing', 'low'),
            ('Loose railing', 'Balcony railing is loose and needs tightening', 'high'),
            ('Pest control needed', 'Seeing cockroaches in kitchen area', 'medium'),
            ('Thermostat not working', 'Cannot adjust temperature, thermostat unresponsive', 'medium'),
            ('Dryer not heating', 'Laundry dryer runs but does not heat up', 'medium'),
            ('Ceiling fan not working', 'Living room ceiling fan not turning on', 'low'),
            ('Screen door torn', 'Screen door has large tear, needs replacement', 'low'),
            ('Outlet not working', 'Electrical outlet in kitchen not providing power', 'medium'),
            ('Shower drain slow', 'Shower draining very slowly, possible clog', 'medium'),
            ('Paint peeling', 'Bathroom ceiling paint peeling due to moisture', 'low'),
            ('Heater making noise', 'Heating system making loud banging noises', 'high'),
        ]

        # Create maintenance requests over last 60 days
        for i, (title, description, priority) in enumerate(maintenance_issues):
            # Random property
            property_obj = random.choice(properties)

            # Find tenant for property (if occupied)
            tenant = None
            active_lease = Lease.objects.filter(property=property_obj, status='active').first()
            if active_lease:
                tenant = active_lease.tenant
            else:
                tenant = random.choice(tenants)

            # Created 0-60 days ago
            created_days_ago = random.randint(0, 60)
            created_at = timezone.now() - timedelta(days=created_days_ago)

            # Status based on age and priority
            if priority == 'urgent':
                if created_days_ago < 2:
                    status = 'in_progress'
                else:
                    status = 'completed'
            elif priority == 'high':
                if created_days_ago < 5:
                    status = 'in_progress'
                elif created_days_ago < 15:
                    status = 'completed'
                else:
                    status = 'completed'
            else:
                if created_days_ago < 3:
                    status = 'open'
                elif created_days_ago < 10:
                    status = 'in_progress'
                else:
                    status = 'completed' if random.random() < 0.7 else 'in_progress'

            completed_at = None
            if status == 'completed':
                completed_at = created_at + timedelta(days=random.randint(1, 7))

            # Assign contractor for in_progress/completed
            assigned_to = ""
            if status in ['in_progress', 'completed']:
                assigned_to = random.choice([
                    'ABC Plumbing', 'Elite HVAC', 'QuickFix Repairs',
                    'Pro Electricians', 'Handyman Services', 'Pest Masters'
                ])

            # Costs
            estimated_cost = None
            actual_cost = None
            if status in ['in_progress', 'completed']:
                estimated_cost = Decimal(str(random.randint(50, 500)))
                if status == 'completed':
                    # Actual cost within 80-120% of estimate
                    variance = random.uniform(0.8, 1.2)
                    actual_cost = Decimal(str(int(float(estimated_cost) * variance)))

            request = MaintenanceRequest.objects.create(
                property=property_obj,
                tenant=tenant,
                title=title,
                description=description,
                priority=priority,
                status=status,
                assigned_to=assigned_to,
                estimated_cost=estimated_cost,
                actual_cost=actual_cost,
                created_at=created_at,
                completed_at=completed_at,
                notes=f"Work completed by {assigned_to}" if status == 'completed' else "",
            )
            requests.append(request)

        return requests

    def _generate_expenses(self, properties):
        """Generate property expenses over last 6 months"""
        expenses = []

        # Common expenses
        expense_templates = [
            ('utilities', 'PG&E Electric Bill', 150, 300),
            ('utilities', 'Water/Sewer Bill', 50, 150),
            ('insurance', 'Property Insurance', 200, 500),
            ('property_tax', 'Quarterly Property Tax', 1000, 3000),
            ('maintenance', 'Landscaping Service', 100, 200),
            ('repairs', 'Plumbing Repair', 150, 800),
            ('repairs', 'HVAC Maintenance', 200, 600),
            ('cleaning', 'Professional Cleaning', 100, 250),
            ('HOA', 'HOA Monthly Fee', 200, 500),
            ('legal', 'Legal Consultation', 300, 1000),
            ('advertising', 'Online Listing Fee', 50, 150),
        ]

        # Generate expenses for each property over last 6 months
        for property_obj in properties:
            # Each property gets 3-6 random expenses
            num_expenses = random.randint(3, 6)

            for _ in range(num_expenses):
                category, description, min_amount, max_amount = random.choice(expense_templates)

                # Random date in last 6 months
                days_ago = random.randint(0, 180)
                expense_date = date.today() - timedelta(days=days_ago)

                amount = Decimal(str(random.randint(min_amount, max_amount)))

                vendor = random.choice([
                    'ABC Services', 'Elite Professionals', 'Pro Solutions',
                    'Quality Services', 'Expert Contractors', 'City Services'
                ])

                expense = Expense.objects.create(
                    property=property_obj,
                    category=category,
                    amount=amount,
                    date=expense_date,
                    description=f"{description} for {property_obj.name}",
                    vendor=vendor,
                    receipt_number=f"RCP{random.randint(1000, 9999)}",
                )
                expenses.append(expense)

        return expenses
