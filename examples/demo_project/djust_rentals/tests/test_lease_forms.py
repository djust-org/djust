"""
Comprehensive tests for Lease CRUD operations with FormMixin

Tests the complete lifecycle:
1. Create a lease
2. Verify creation
3. Edit the lease
4. Verify edit (no duplicate created)
5. Delete the lease
6. Verify deletion
"""

from django.test import TestCase, Client
from django.urls import reverse
from datetime import date, timedelta
from decimal import Decimal

from djust_rentals.models import Lease, Property, Tenant
from djust_rentals.forms import LeaseForm
from django.contrib.auth.models import User


class LeaseFormCRUDTestCase(TestCase):
    """Test complete CRUD cycle for leases with FormMixin"""

    def setUp(self):
        """Set up test data"""
        # Create a user for the tenant
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123',
            first_name='John',
            last_name='Doe'
        )

        # Create a property
        self.property = Property.objects.create(
            name='Test Property',
            address='123 Test St',
            city='Test City',
            state='TS',
            zip_code='12345',
            property_type='apartment',
            bedrooms=2,
            bathrooms=1,
            square_feet=1000,
            monthly_rent=Decimal('1500.00'),
            security_deposit=Decimal('1500.00'),
            status='available'
        )

        # Create a tenant
        self.tenant = Tenant.objects.create(
            user=self.user,
            phone='555-1234',
            emergency_contact_name='Jane Doe',
            emergency_contact_phone='555-5678'
        )

        # Set up dates
        self.start_date = date.today() + timedelta(days=7)
        self.end_date = self.start_date + timedelta(days=365)

        # Test data for lease creation
        self.lease_data = {
            'property': str(self.property.pk),
            'tenant': str(self.tenant.pk),
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat(),
            'monthly_rent': '1500.00',
            'security_deposit': '1500.00',
            'rent_due_day': '1',
            'late_fee': '50.00',
            'status': 'upcoming',
            'terms': 'Standard rental agreement'
        }

        self.client = Client()

    def test_01_create_lease(self):
        """Test creating a new lease"""
        print("\n=== Test 1: Create Lease ===")

        # Get initial count
        initial_count = Lease.objects.count()
        print(f"Initial lease count: {initial_count}")

        # Create lease using Django form directly
        form = LeaseForm(data=self.lease_data)
        self.assertTrue(form.is_valid(), f"Form errors: {form.errors}")

        # Save the lease
        lease = form.save()
        print(f"Created lease ID: {lease.pk}")

        # Verify lease was created
        final_count = Lease.objects.count()
        print(f"Final lease count: {final_count}")

        self.assertEqual(final_count, initial_count + 1, "Lease count should increase by 1")
        self.assertEqual(lease.property, self.property)
        self.assertEqual(lease.tenant, self.tenant)
        self.assertEqual(lease.monthly_rent, Decimal('1500.00'))
        self.assertEqual(lease.status, 'upcoming')

        print(f"✓ Lease created successfully: {lease}")

        # Store lease ID for next tests
        self.created_lease_id = lease.pk
        return lease

    def test_02_verify_lease_exists(self):
        """Test that created lease exists and has correct data"""
        print("\n=== Test 2: Verify Lease Exists ===")

        # Create a lease first
        lease = self.test_01_create_lease()

        # Retrieve the lease
        retrieved_lease = Lease.objects.get(pk=lease.pk)
        print(f"Retrieved lease ID: {retrieved_lease.pk}")

        # Verify all fields
        self.assertEqual(retrieved_lease.property, self.property)
        self.assertEqual(retrieved_lease.tenant, self.tenant)
        self.assertEqual(retrieved_lease.start_date, self.start_date)
        self.assertEqual(retrieved_lease.end_date, self.end_date)
        self.assertEqual(retrieved_lease.monthly_rent, Decimal('1500.00'))
        self.assertEqual(retrieved_lease.security_deposit, Decimal('1500.00'))
        self.assertEqual(retrieved_lease.rent_due_day, 1)
        self.assertEqual(retrieved_lease.late_fee, Decimal('50.00'))
        self.assertEqual(retrieved_lease.status, 'upcoming')
        self.assertEqual(retrieved_lease.terms, 'Standard rental agreement')

        print("✓ All lease fields verified correctly")

    def test_03_edit_lease_no_duplicate(self):
        """Test editing a lease does NOT create a duplicate"""
        print("\n=== Test 3: Edit Lease (No Duplicate) ===")

        # Create a lease first
        lease = self.test_01_create_lease()
        initial_count = Lease.objects.count()
        print(f"Initial lease count: {initial_count}")
        print(f"Editing lease ID: {lease.pk}")

        # Modify lease data
        updated_data = self.lease_data.copy()
        updated_data['monthly_rent'] = '1600.00'
        updated_data['security_deposit'] = '1600.00'
        updated_data['status'] = 'active'

        # Edit lease using form with instance (simulating FormMixin behavior)
        form = LeaseForm(data=updated_data, instance=lease)
        self.assertTrue(form.is_valid(), f"Form errors: {form.errors}")

        # Save the edited lease
        saved_lease = form.save()
        print(f"Saved lease ID: {saved_lease.pk}")

        # Verify no duplicate was created
        final_count = Lease.objects.count()
        print(f"Final lease count: {final_count}")

        self.assertEqual(final_count, initial_count,
                        f"Lease count should NOT change. Expected {initial_count}, got {final_count}")

        # Verify it's the same lease (same PK)
        self.assertEqual(saved_lease.pk, lease.pk,
                        f"Lease ID should be the same. Expected {lease.pk}, got {saved_lease.pk}")

        # Verify the changes were applied
        updated_lease = Lease.objects.get(pk=lease.pk)
        self.assertEqual(updated_lease.monthly_rent, Decimal('1600.00'))
        self.assertEqual(updated_lease.security_deposit, Decimal('1600.00'))
        self.assertEqual(updated_lease.status, 'active')

        print(f"✓ Lease updated successfully (no duplicate created)")
        print(f"  - Old rent: $1500.00 → New rent: ${updated_lease.monthly_rent}")
        print(f"  - Old status: upcoming → New status: {updated_lease.status}")

    def test_04_edit_lease_verify_data(self):
        """Test that edited lease has correct updated data"""
        print("\n=== Test 4: Verify Edited Lease Data ===")

        # Create and edit a lease
        lease = self.test_01_create_lease()

        # Edit the lease
        updated_data = self.lease_data.copy()
        updated_data['monthly_rent'] = '1700.00'
        updated_data['late_fee'] = '75.00'
        updated_data['terms'] = 'Updated rental agreement'

        form = LeaseForm(data=updated_data, instance=lease)
        self.assertTrue(form.is_valid())
        form.save()

        # Retrieve and verify
        updated_lease = Lease.objects.get(pk=lease.pk)

        self.assertEqual(updated_lease.monthly_rent, Decimal('1700.00'))
        self.assertEqual(updated_lease.late_fee, Decimal('75.00'))
        self.assertEqual(updated_lease.terms, 'Updated rental agreement')

        # Verify unchanged fields remain the same
        self.assertEqual(updated_lease.property, self.property)
        self.assertEqual(updated_lease.tenant, self.tenant)
        self.assertEqual(updated_lease.start_date, self.start_date)

        print("✓ Edited lease data verified correctly")
        print(f"  - Monthly rent: ${updated_lease.monthly_rent}")
        print(f"  - Late fee: ${updated_lease.late_fee}")

    def test_05_delete_lease(self):
        """Test deleting a lease"""
        print("\n=== Test 5: Delete Lease ===")

        # Create a lease
        lease = self.test_01_create_lease()
        lease_id = lease.pk
        initial_count = Lease.objects.count()
        print(f"Initial lease count: {initial_count}")
        print(f"Deleting lease ID: {lease_id}")

        # Delete the lease
        lease.delete()

        # Verify deletion
        final_count = Lease.objects.count()
        print(f"Final lease count: {final_count}")

        self.assertEqual(final_count, initial_count - 1, "Lease count should decrease by 1")

        # Verify lease no longer exists
        with self.assertRaises(Lease.DoesNotExist):
            Lease.objects.get(pk=lease_id)

        print(f"✓ Lease deleted successfully")

    def test_06_complete_crud_cycle(self):
        """Test complete CRUD cycle in sequence"""
        print("\n=== Test 6: Complete CRUD Cycle ===")

        # 1. CREATE
        print("Step 1: CREATE")
        initial_count = Lease.objects.count()
        form = LeaseForm(data=self.lease_data)
        self.assertTrue(form.is_valid())
        lease = form.save()
        self.assertEqual(Lease.objects.count(), initial_count + 1)
        print(f"  ✓ Created lease ID: {lease.pk}")

        # 2. READ/VERIFY
        print("Step 2: READ/VERIFY")
        retrieved_lease = Lease.objects.get(pk=lease.pk)
        self.assertEqual(retrieved_lease.monthly_rent, Decimal('1500.00'))
        print(f"  ✓ Verified lease data")

        # 3. UPDATE/EDIT
        print("Step 3: UPDATE/EDIT")
        count_before_edit = Lease.objects.count()
        updated_data = self.lease_data.copy()
        updated_data['monthly_rent'] = '1800.00'
        form = LeaseForm(data=updated_data, instance=lease)
        self.assertTrue(form.is_valid())
        updated_lease = form.save()
        count_after_edit = Lease.objects.count()

        # Critical: verify no duplicate created
        self.assertEqual(count_after_edit, count_before_edit,
                        f"EDIT CREATED DUPLICATE! Count before: {count_before_edit}, after: {count_after_edit}")
        self.assertEqual(updated_lease.pk, lease.pk,
                        f"EDIT CHANGED PK! Original: {lease.pk}, after edit: {updated_lease.pk}")
        print(f"  ✓ Updated lease (no duplicate)")
        print(f"    - Same ID: {updated_lease.pk}")
        print(f"    - Count unchanged: {count_after_edit}")

        # 4. VERIFY UPDATE
        print("Step 4: VERIFY UPDATE")
        lease_after_edit = Lease.objects.get(pk=lease.pk)
        self.assertEqual(lease_after_edit.monthly_rent, Decimal('1800.00'))
        print(f"  ✓ Verified update: rent is ${lease_after_edit.monthly_rent}")

        # 5. DELETE
        print("Step 5: DELETE")
        lease_id = lease.pk
        lease.delete()
        self.assertEqual(Lease.objects.count(), count_before_edit - 1)
        print(f"  ✓ Deleted lease ID: {lease_id}")

        # 6. VERIFY DELETION
        print("Step 6: VERIFY DELETION")
        with self.assertRaises(Lease.DoesNotExist):
            Lease.objects.get(pk=lease_id)
        print(f"  ✓ Verified deletion")

        print("\n✅ Complete CRUD cycle passed successfully!")

    def test_07_form_validation(self):
        """Test form validation rules"""
        print("\n=== Test 7: Form Validation ===")

        # Test: End date must be after start date
        invalid_data = self.lease_data.copy()
        invalid_data['end_date'] = invalid_data['start_date']  # Same as start

        form = LeaseForm(data=invalid_data)
        self.assertFalse(form.is_valid())
        self.assertIn('end_date', form.errors)
        print("✓ Validation: end_date must be after start_date")

        # Test: Lease must be at least 30 days
        invalid_data['end_date'] = (self.start_date + timedelta(days=20)).isoformat()
        form = LeaseForm(data=invalid_data)
        self.assertFalse(form.is_valid())
        self.assertIn('end_date', form.errors)
        print("✓ Validation: lease must be at least 30 days")

        # Test: Start date cannot be in the past (for new leases)
        past_data = self.lease_data.copy()
        past_data['start_date'] = (date.today() - timedelta(days=10)).isoformat()
        form = LeaseForm(data=past_data)
        self.assertFalse(form.is_valid())
        self.assertIn('start_date', form.errors)
        print("✓ Validation: start_date cannot be in past for new leases")

    def test_08_concurrent_edits(self):
        """Test that multiple edits don't create duplicates"""
        print("\n=== Test 8: Multiple Edits (No Duplicates) ===")

        # Create a lease
        lease = self.test_01_create_lease()
        initial_count = Lease.objects.count()
        print(f"Initial count: {initial_count}")

        # Perform multiple edits
        for i in range(5):
            updated_data = self.lease_data.copy()
            updated_data['monthly_rent'] = str(1500 + (i * 100))

            form = LeaseForm(data=updated_data, instance=lease)
            self.assertTrue(form.is_valid())
            lease = form.save()

            # Verify count hasn't changed
            current_count = Lease.objects.count()
            self.assertEqual(current_count, initial_count,
                           f"Edit {i+1} created duplicate! Count: {current_count}")
            print(f"  Edit {i+1}: rent=${updated_data['monthly_rent']} - No duplicate ✓")

        print(f"✓ All 5 edits completed without creating duplicates")
        print(f"  Final count: {Lease.objects.count()} (unchanged)")


def run_tests():
    """Helper function to run tests with verbose output"""
    import unittest

    # Create test suite
    suite = unittest.TestLoader().loadTestsFromTestCase(LeaseFormCRUDTestCase)

    # Run with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Print summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    print(f"Tests run: {result.testsRun}")
    print(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print("="*70)

    return result.wasSuccessful()


if __name__ == '__main__':
    run_tests()
