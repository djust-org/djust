"""
StatusChange Form Definition

Defines the declarative sections for the StatusChange form, adapted from the original
PersonnelChangeOfStatus form but mapped to the new StatusChange model fields.

Field Mappings (PersonnelChangeOfStatus → StatusChange):
- state_of_residence → project_site_state
- division → business_line (but we'll keep using 'division' in the definition)
- All other fields mostly map 1:1 with some name changes

This definition will be consumed by the React frontend via the form-definition API.
"""

from typing import Dict, Any
from django.apps import apps
from django.contrib.auth.models import User
from .base import (
    FormSection,
    FormField,
    when,
    when_field_equals,
    when_field_contains,
    ENV_CIVIL_FUELS_DIVISIONS,
    CONST_DIVISIONS,
    get_field_metadata_with_options,
    build_field_dependency_mappings
)


def handle_employee_change(instance, old_value, new_value):
    """
    Handler called when the employee field changes.
    Auto-populates fields from the selected employee's profile.

    Args:
        instance: The StatusChange instance being updated
        old_value: Previous employee ID (or None)
        new_value: New employee ID

    Returns:
        Dict of field names and values to auto-populate
    """
    populated = {}

    if new_value:
        try:
            user = User.objects.get(id=new_value)

            if hasattr(user, 'profile') and user.profile:
                # Auto-populate business_line from employee's profile
                if user.profile.business_line_id:
                    populated['business_line'] = user.profile.business_line_id

                # Auto-populate previous_supervisor for To/From section
                if user.profile.supervisor_id:
                    populated['previous_supervisor'] = user.profile.supervisor_id

                # TODO: Add more fields as they're added to Profile model:
                # - previous_office when profile.office is added
                # - previous_pay_class when profile.pay_class is added
                # - previous_company when profile.company is added
                # etc.

        except User.DoesNotExist:
            # Employee not found, don't populate anything
            pass

    return populated


class StatusChangeDefinition:
    """
    Pure form definition for StatusChange - no Django form inheritance.
    Maps the original PersonnelChangeOfStatus declarative sections to StatusChange model.
    """

    @staticmethod
    def get_model_name() -> str:
        return "StatusChange"

    @staticmethod
    def get_declarative_sections() -> Dict[str, FormSection]:
        """
        Define the complete form structure with conditional logic.
        Adapted from ObjectBasedPersonnelChangeForm.declarative_sections
        """
        return {
            "behalf_of": FormSection(
                title="Submit on Behalf of Another User",
                icon="fas fa-user-friends",
                expanded=True,
                section_type="regular",  # RegularSection component
                permission_required=[
                    "can_create_on_behalf_of_another_user",
                    "web_internal_admins",
                ],
            ).add_field("on_behalf_of", FormField(label="On Behalf Of").with_size("medium")),

            "employee_information": FormSection(
                title="Employee Information",
                icon="fas fa-user",
                expanded=True,
                section_type="regular"  # RegularSection component with blue variant
            )
            .add_field("employee",
                       FormField(size="large", label="Employee").make_required().on_change(handle_employee_change))
            .add_field("business_line", FormField(size="large", label="Business Line").make_required())
            .add_field(
                "executive_approver",
                FormField(
                    size="large",
                    label="Executive Approver",
                    depends_on="business_line",  # Filter based on selected business line
                    filter_by="executive_approvers,secondary_executive_approvers"  # M2M fields to include
                ).make_required()
            )
            .add_field("effective_date", FormField(size="large", label="Effective Date").make_required()),

            "company_changes": FormSection(
                title="Change Company",
                icon="fas fa-building",
                expanded=True,
                needs_field="needs_company",
                section_type="tofrom",  # ToFrom component
            )
            .add_field("needs_company", FormField(size="small", label="Change Company?"))
            .add_field("previous_company", FormField(size="medium", label="Previous Company"))
            .add_field("new_company", FormField(size="medium", label="New Company").make_highlighted()),

            "business_line_changes": FormSection(
                title="Change Business Line",
                icon="fas fa-sitemap",
                expanded=True,
                needs_field="needs_business_line",
                section_type="tofrom",  # ToFrom component
            )
            .add_field("needs_business_line", FormField(size="small", label="Change Business Line?"))
            .add_field("previous_business_line", FormField(size="medium", label="Previous Business Line"))
            .add_field("new_business_line", FormField(size="medium", label="New Business Line").make_highlighted()),

            "job_title_changes": FormSection(
                title="Change Job Title",
                icon="fas fa-briefcase",
                expanded=True,
                needs_field="needs_job_title",
                section_type="tofrom",  # ToFrom component
            )
            .add_field("needs_job_title", FormField(size="small", label="Change Job Title?"))
            .add_field("previous_job_title", FormField(size="medium", label="Previous Job Title"))
            .add_field("new_job_title", FormField(size="medium", label="New Job Title").make_highlighted()),

            "office_changes": FormSection(
                title="Change Office",
                icon="fas fa-building",
                expanded=True,
                needs_field="needs_office",
                section_type="tofrom",  # ToFrom component
            )
            .add_field("needs_office", FormField(size="small", label="Change Office?"))
            .add_field("previous_office_location", FormField(size="medium", label="Previous Office Location"))
            .add_field("new_office_location", FormField(size="medium", label="New Office Location").make_highlighted())
            .add_field("office_type", FormField(size="wide", label="Office Type")),

            "work_site_changes": FormSection(
                title="Change Work Site Address",
                icon="fas fa-map-marker-alt",
                expanded=True,
                needs_field="needs_work_site_address",
                section_type="tofrom",  # ToFrom component
            )
            .add_field("needs_work_site_address", FormField(size="small", label="Change Work Site Address?"))
            .add_field("previous_work_site_address", FormField(size="large", label="Previous Work Site Address"))
            .add_field("new_work_site_address",
                       FormField(size="large", label="New Work Site Address").make_highlighted()),

            "supervisor_changes": FormSection(
                title="Change Supervisor",
                icon="fas fa-users",
                expanded=True,
                needs_field="needs_supervisor",
                section_type="tofrom",  # ToFrom component
            )
            .add_field("needs_supervisor", FormField(size="small", label="Change Supervisor?"))
            .add_field("previous_supervisor", FormField(size="medium", label="Previous Supervisor"))
            .add_field("new_supervisor", FormField(size="medium", label="New Supervisor").make_highlighted()),

            "timesheet_approval_changes": FormSection(
                title="Change Timesheet Approver",
                icon="fas fa-clock",
                expanded=True,
                needs_field="needs_timesheet_approver",
                section_type="tofrom",  # ToFrom component
            )
            .add_field("needs_timesheet_approver", FormField(size="small", label="Change Timesheet Approver?"))
            .add_field("previous_supervisor_who_approved_timesheet",
                       FormField(size="medium", label="Previous Timesheet Approver"))
            .add_field("new_supervisor_to_approve_timesheet",
                       FormField(size="medium", label="New Timesheet Approver").make_highlighted()),

            "pay_rate_changes": FormSection(
                title="Pay Rate Changes",
                icon="fas fa-dollar-sign",
                expanded=True,
                needs_field="needs_pay_rate",
                section_type="tofrom",  # ToFrom component
            )
            .add_field("needs_pay_rate", FormField(size="small", label="Change Pay Rate?"))
            .add_field("previous_pay_rate", FormField(size="medium", label="Previous Pay Rate"))
            .add_field("new_pay_rate", FormField(size="medium", label="New Pay Rate").make_highlighted()),

            "pay_class_changes": FormSection(
                title="Pay Class Changes",
                icon="fas fa-dollar-sign",
                expanded=True,
                needs_field="needs_pay_class",
                section_type="tofrom",  # ToFrom component
            )
            .add_field("needs_pay_class", FormField(size="small", label="Change Pay Class?"))
            .add_field("previous_pay_class", FormField(size="medium", label="Previous Pay Class"))
            .add_field("new_pay_class", FormField(size="medium", label="New Pay Class").make_highlighted()),

            "pay_cycle_changes": FormSection(
                title="Pay Cycle Changes",
                icon="fas fa-dollar-sign",
                expanded=True,
                needs_field="needs_pay_cycle",
                section_type="tofrom",  # ToFrom component
            )
            .add_field("needs_pay_cycle", FormField(size="small", label="Change Pay Cycle?"))
            .add_field("previous_pay_cycle", FormField(size="medium", label="Previous Pay Cycle"))
            .add_field("new_pay_cycle", FormField(size="medium", label="New Pay Cycle").make_highlighted()),

            "employment_status_changes": FormSection(
                title="Employment Status Changes",
                icon="fas fa-user-check",
                expanded=True,
                needs_field="needs_employment_status",
                section_type="tofrom",  # ToFrom component
            )
            .add_field("needs_employment_status", FormField(size="small", label="Change Employment Status?"))
            .add_field("previous_employment_status", FormField(size="medium", label="Previous Employment Status"))
            .add_field("new_employment_status",
                       FormField(size="medium", label="New Employment Status").make_highlighted()),

            "compliance_information": FormSection(
                title="Compliance Information",
                icon="fas fa-shield-alt",
                expanded=True,
                needs_field="needs_sca_db",  # Updated to match StatusChange model
                section_type="needs"  # NeedsSection component
            )
            .add_field("needs_sca_db", FormField(size="small", label="Add Compliance Info?"))
            .add_field("sca", FormField(size="medium", label="Is Employee SCA?"))
            .add_field("davis_bacon", FormField(size="medium", label="Is Employee Davis Bacon?"))
            .add_field("health_and_welfare", FormField(size="medium", label="Health and Welfare ($/hr)")
                       .when(when("sca").equals(True, required=True))),

            # Import shared sections from common components
            "project_information": StatusChangeDefinition._get_project_information_section(),
            "meals_per_diem": StatusChangeDefinition._get_meals_per_diem_section(),
            "lodging_per_diem": StatusChangeDefinition._get_lodging_per_diem_section(),
            "vehicle_information": StatusChangeDefinition._get_vehicle_information_section(),
            "allowances": StatusChangeDefinition._get_allowances_section(),

            "additional_notes": FormSection(
                title="Add Additional Notes",
                icon="fas fa-info-circle",
                expanded=True,
                section_type="regular"  # RegularSection component
            ).add_field("notes", FormField(size="wide", label="Additional Notes")),

            "supervisor_signature": FormSection(
                title="Supervisor Signature",
                icon="fas fa-signature",
                expanded=True,
                section_type="regular"  # RegularSection component with blue variant
            ).add_field("supervisor_signature", FormField(size="wide", label="Supervisor Signature").make_required()),
        }

    @staticmethod
    def get_related_fields() -> Dict[str, Dict[str, Any]]:
        """Define field dependencies (like executive_approver depends on business_line)"""
        return {
            "executive_approver": {
                "field": "business_line",  # Updated to use business_line instead of division
                "related_method": "get_related_executive_approvers_from_business_line",
            },
        }

    @staticmethod
    def get_validation_rules() -> Dict[str, Any]:
        """Define form validation rules"""
        return {
            "always_required": [
                "effective_date",
                "employee",
                "business_line",
                "executive_approver",
                "supervisor_signature"
            ],
            "conditional_required": {
                "new_company": "needs_company === true",
                "new_business_line": "needs_business_line === true",
                "new_job_title": "needs_job_title === true && new_job_title.trim() !== ''",
                "new_office_location": "needs_office === true",
                "new_work_site_address": "needs_work_site_address === true",
                "new_supervisor": "needs_supervisor === true",
                "new_supervisor_to_approve_timesheet": "needs_timesheet_approver === true",
                "new_pay_rate": "needs_pay_rate === true",
                "new_pay_class": "needs_pay_class === true",
                "new_pay_cycle": "needs_pay_cycle === true",
                "new_employment_status": "needs_employment_status === true",
                "health_and_welfare": "needs_sca_db === true && sca === true"
            }
        }

    @staticmethod
    def _get_project_information_section() -> FormSection:
        """Project information section with conditional logic"""
        return (
            FormSection(
                title="Add Project Information",
                icon="fas fa-clipboard-list",
                expanded=True,
                needs_field="needs_projects",
                section_type="needs",  # NeedsSection component
            )
            .add_field("needs_projects", FormField(size="small", label="Add Project Information?"))
            .add_field("project_number", FormField(size="large", label="Project Number"))
            .add_field("project_name", FormField(size="large", label="Project Name"))
            .add_field("project_duration", FormField(size="large", label="Project Duration"))
            .add_field(
                "expected_end_date",
                FormField(size="large")
                .with_label("Expected End Date")
                .when(
                    when("project_duration").contains(  # Updated field name
                        ["Exceeds 12 Months"],
                        label="Project Expected End Date",
                        help_text="Enter the anticipated completion date for this long-term project",
                        required=True,
                    )
                ),
            )
            .add_field("local_hire",
                       FormField(size="large", label="Is Employee staying overnight and receiving per diem?"))
            .add_field("project_site_address", FormField(size="large", label="Project Site Address"))
            .add_field("project_site_city", FormField(size="medium", label="Project Site City"))
            .add_field("project_site_state", FormField(size="medium", label="Project Site State"))
            .add_field("project_site_zipcode", FormField(size="medium", label="Project Site ZIP Code"))
        )

    @staticmethod
    def _get_meals_per_diem_section() -> FormSection:
        """Meals per diem section with complex conditional logic"""
        section = FormSection(
            title="Add Meals Per Diem",
            icon="fas fa-utensils",
            expanded=True,
            needs_field="needs_meals_per_diem",
            section_type="needs",  # NeedsSection component
        )

        # Dynamic section titles based on division and duration
        section.when({
            "field_name": "business_line",  # Updated field name
            "label_contains": ENV_CIVIL_FUELS_DIVISIONS,
            "field_name_2": "project_duration",  # Updated field name
            "label_contains_2": ["Under 12 Months"],
            "title": "MEALS AND INCIDENTALS PER DIEM: Duration < 12 Months, non-taxable via Stampli",
            "logic": "AND",
        })

        section.when({
            "field_name": "business_line",  # Updated field name
            "label_contains": ENV_CIVIL_FUELS_DIVISIONS,
            "field_name_2": "project_duration",  # Updated field name
            "label_contains_2": ["Exceeds 12 Months"],
            "title": "MEALS AND INCIDENTALS PER DIEM: Duration > 12 Months, taxable via Stampli",
            "logic": "AND",
        })

        return (
            section
            .add_field("needs_meals_per_diem", FormField(size="wide", label="Employee needs meals per diem?"))
            .add_field(
                "gross_up_meals_per_diem",
                FormField(size="wide")
                .with_label("Gross Up for Taxes")
                .when(
                    when("business_line").contains(ENV_CIVIL_FUELS_DIVISIONS),  # Updated field name
                    when("project_duration").contains(["Exceeds 12 Months"],  # Updated field name
                                                      label="Grossed up for taxes?",
                                                      help_text="Select YES if company covers tax burden, NO if employee pays taxes",
                                                      ),
                )
            )
            .add_field(
                "meals_per_diem_gsa_jtr",
                FormField(size="large")
                .with_label("Use GSA/JTR rates for meals per diem")
                .when(
                    when("business_line").contains(ENV_CIVIL_FUELS_DIVISIONS),  # Updated field name
                    when("project_duration").contains(["Under 12 Months"],  # Updated field name
                                                      label="JTR/GSA Per Diem",
                                                      help_text="YES if rate depends on where employee works. NO for custom details.",
                                                      ),
                )
            )
            .add_field(
                "per_diem_when_employee_reports_to_job_site",
                FormField(size="large")
                .with_label("Employee Report Date")
                .when(
                    when("business_line").contains(CONST_DIVISIONS),  # Updated field name
                    when("project_duration").contains(["Exceeds 12 Months"],  # Updated field name
                                                      label="Date Employee Reports to job site",
                                                      help_text="Needed if employee not immediately reporting to assigned job site.",
                                                      ),
                ),
            )
            .add_field(
                "days_per_month_on_job_site",  # Updated field name to match StatusChange model
                FormField(size="large")
                .with_label("Days Per Month on Jobsite")
                .when(
                    when("business_line").contains(ENV_CIVIL_FUELS_DIVISIONS),  # Updated field name
                    when("project_duration").contains(["Exceeds 12 Months"]),  # Updated field name
                    when("meals_per_diem_gsa_jtr").equals(True,
                                                          label="Number of Days Per Month",
                                                          help_text="Enter number of days per month (typically 30 for full-time)",
                                                          required=True,
                                                          ),
                )
            )
            .add_field(
                "new_meals_per_diem_amount",
                FormField(size="large")
                .with_label("New Meals Per Diem Amount")
                .when(
                    when("business_line").contains(ENV_CIVIL_FUELS_DIVISIONS),  # Updated field name
                    when("project_duration").contains(["Exceeds 12 Months"]),  # Updated field name
                    when("meals_per_diem_gsa_jtr").equals(True,
                                                          label="Per Diem Rate for Area",
                                                          help_text="Enter custom meals per diem amount for this project",
                                                          required=True,
                                                          ),
                )
            )
            .add_field(
                "total_monthly_meals_per_diem",
                FormField(size="large")
                .with_label("Total Monthly Meals Per Diem")
                .with_calculation(
                    "(data.days_per_month_on_job_site || 0) * (data.new_meals_per_diem_amount || 0)",
                    depends_on=[
                        "days_per_month_on_job_site",  # Updated field name
                        "new_meals_per_diem_amount",
                    ],
                )
                .when(
                    when("business_line").contains(ENV_CIVIL_FUELS_DIVISIONS),  # Updated field name
                    when("project_duration").contains(["Exceeds 12 Months"],  # Updated field name
                                                      label="Monthly Per Diem Amount",
                                                      help_text="Monthly M&I per diem submitted in Stampli (typically split into two submissions)",
                                                      ),
                )
            )
            .add_field(
                "meals_cost_code",
                FormField(size="large")
                .with_label("Meals Cost Code")
                .when(
                    when("business_line").contains(ENV_CIVIL_FUELS_DIVISIONS,  # Updated field name
                                                   label="Cost Code",
                                                   help_text="Required: C01.21.01 Employee Travel & Per Diem",
                                                   ),
                )
            )
            .add_field(
                "meals_per_diem_details",
                FormField(size="wide")
                .with_label("Meals Per Diem Details")
                .when(
                    when("business_line").contains(ENV_CIVIL_FUELS_DIVISIONS),  # Updated field name
                    when("project_duration").contains(["Under 12 Months"]),  # Updated field name
                    when("meals_per_diem_gsa_jtr").equals(False,
                                                          label="If no, provide details below",
                                                          help_text="Example: Employee will receive $55/day for food for duration of project",
                                                          ),
                )
            )
        )

    @staticmethod
    def _get_lodging_per_diem_section() -> FormSection:
        """Lodging per diem section with complex conditional logic"""
        return (
            FormSection(
                title="Add Lodging Per Diem",
                icon="fas fa-home",
                expanded=True,
                needs_field="needs_lodging_per_diem",
                section_type="needs",  # NeedsSection component
            )
            .add_field("needs_lodging_per_diem", FormField(size="small"))
            .add_field(
                "gross_up_lodging_per_diem",
                FormField(size="wide")
                .with_label("Gross Up Lodging for Taxes")
                .when(
                    when("project_duration").contains(["Exceeds 12 Months"],  # Updated field name
                                                      label="Grossed up for taxes?",
                                                      help_text="YES if company covers tax burden, NO if employee pays taxes",
                                                      )
                ),
            )
            .add_field(
                "what_type_of_lodging_is_offered",
                FormField(size="wide")
                .with_label("Type of Lodging Offered")
                .when(
                    when("business_line").contains(CONST_DIVISIONS,  # Updated field name
                                                   label="What type of lodging is being provided?",
                                                   help_text="Hotel | Apartment | RV Lot | AirBNB | Other",
                                                   )
                ),
            )
            .add_field(
                "new_lodging_per_diem_amount",
                FormField(size="large", highlight=False)
                .with_label("Monthly Lodging Per Diem Amount")
                .when(
                    when("business_line").contains(CONST_DIVISIONS),  # Updated field name
                    when("project_duration").contains(["Exceeds 12 Months"],  # Updated field name
                                                      label="Monthly Not to Exceed Amount",
                                                      help_text="Calculated total monthly lodging per diem amount",
                                                      ),
                ),
            )
            .add_field(
                "daily_lodging_per_diem_amount",
                FormField(size="large")
                .with_label("Per Diem Rate for Area")
                .when(
                    when("business_line").contains(ENV_CIVIL_FUELS_DIVISIONS),  # Updated field name
                    when("project_duration").contains(["Exceeds 12 Months"]),  # Updated field name
                ),
            )
            .add_field(
                "total_monthly_lodging_per_diem",
                FormField(size="large")
                .with_label("Total Monthly Lodging Per Diem")
                .with_calculation(
                    # Convert complex lambda to JavaScript expression
                    "data.new_lodging_per_diem_amount || ((data.lodging_number_of_days_per_month || 0) * (data.daily_lodging_per_diem_amount || 0))",
                    depends_on=[
                        "new_lodging_per_diem_amount",
                        "lodging_number_of_days_per_month",
                        "daily_lodging_per_diem_amount",
                        "business_line",  # Updated field name
                        "project_duration",  # Updated field name
                    ],
                )
            )
            .add_field("lodging_per_diem_details", FormField(size="wide"))
        )

    @staticmethod
    def _get_vehicle_information_section() -> FormSection:
        """Vehicle information section"""
        return (
            FormSection(
                title="Add Vehicle",
                icon="fas fa-truck",
                expanded=True,
                needs_field="needs_vehicle",
                section_type="needs",  # NeedsSection component
            )
            .add_field("needs_vehicle", FormField(size="small", label="Add Vehicle?"))
            .add_field("vehicle_cost_code", FormField(size="medium", label="Vehicle Cost Code"))
        )

    @staticmethod
    def _get_allowances_section() -> FormSection:
        """Allowances section with vehicle allowance fields"""
        return (
            FormSection(
                title="Add Monthly Vehicle Allowance",
                icon="fas fa-money-bill-wave",
                expanded=True,
                needs_field="needs_allowances",
                section_type="needs",  # NeedsSection component
            )
            .add_field("needs_allowances", FormField(size="small", label="Add Vehicle Allowance?"))
            .add_field("monthly_vehicle_allowance", FormField(size="medium", label="Monthly Vehicle Allowance"))
            .add_field("monthly_vehicle_allowance_cost_code",
                       FormField(size="medium", label="Vehicle Allowance Cost Code"))
            .add_field("vehicle_allowance_gross_up", FormField(size="medium", label="Gross Up Vehicle Allowance"))
        )

    @classmethod
    def to_react_config(cls) -> Dict[str, Any]:
        """
        Convert the entire form definition to React-consumable JSON.

        Now includes field_metadata with types and options - everything needed
        for the form in a single API response.

        Also includes field_dependencies for dependent dropdowns (e.g., executive_approver
        filtered by business_line).
        """
        sections = cls.get_declarative_sections()
        model_name = cls.get_model_name()

        # Get the Django model
        model = apps.get_model(app_label="website", model_name=model_name)

        # Build sections dict
        sections_dict = {
            section_id: section.to_dict()
            for section_id, section in sections.items()
        }

        # Build field dependency mappings (generic, reusable)
        field_dependencies = build_field_dependency_mappings(
            model,
            {"sections": list(sections_dict.values())}
        )

        return {
            "model_name": model_name,
            "sections": sections_dict,
            "related_fields": cls.get_related_fields(),
            "validation_rules": cls.get_validation_rules(),
            # ✅ Include complete field metadata with options
            "field_metadata": get_field_metadata_with_options(model_name),
            # ✅ NEW: Include generic field dependency mappings
            "field_dependencies": field_dependencies
        }