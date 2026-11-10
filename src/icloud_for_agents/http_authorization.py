"""Server-owned HTTP client permissions, independent of MCP advisory hints."""
from contextvars import ContextVar

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware

from .approvals import PlanStore
from .core import SafetyError


SERVICES = ("mail", "calendar", "contacts", "notes", "reminders")
PRINCIPAL = ContextVar("icloud_http_client", default=None)

# This is an explicit authorization inventory, not a trust decision based on a
# tool's readOnlyHint. New tools remain unavailable over HTTP until classified.
READ_TOOLS = frozenset("""
email_list_folders email_page email_inspect email_plan_status email_operation_status
email_operations email_observe_operation email_cleanup_grant_status
calendar_list calendar_events contacts_addressbooks contacts_list contacts_get
reminders_access_status reminders_status reminders_sources reminders_lists
reminders_default_list reminders_list_sections reminders_inspect_group_hierarchy
reminders_group_inventory reminders_group_lists reminders_list_parent
reminders_list_hierarchy reminders_list_order reminders_list_features
reminders_smart_list_definition reminders_template_outline reminders_query
reminders_daily reminders_export_page reminders_export_ical reminders_get
reminders_export_attachment reminders_inspect_recovery_hierarchy
reminders_template_item_details reminders_template_metadata reminders_template_sections
reminders_smart_lists reminders_templates reminders_recovery_list reminders_recovery_tree
reminders_recovery_lookup reminders_inspect_hierarchy reminders_grant_status
reminders_plan_status reminders_inspect_uncertain_change
notes_format_plain_table notes_format_list_markdown notes_model_folder_role
notes_folder_role notes_smart_folder_definition notes_recovery_folder notes_access_status
notes_status notes_sources notes_default_folder notes_locate notes_folders notes_query
notes_search notes_native_metadata notes_native_query notes_native_assets notes_native_tags
notes_native_checklist notes_ocr_image_attachment notes_export_fallback_pdf
notes_export_attachment notes_attachments notes_read_text notes_get
notes_inspect_exported_links notes_find_html_references notes_export_observation
notes_grant_status notes_inspect_uncertain_edit notes_plan_status policy_status
""".split())
WRITE_TOOLS = frozenset("""
email_set_read_state email_archive email_prepare_trash email_restore_from_trash
email_prepare_send email_execute_approved email_prepare_recovery email_execute_recovery
email_prepare_cleanup_grant email_activate_cleanup_grant email_revoke_cleanup_grant
calendar_prepare_changes calendar_prepare_once contacts_prepare_changes
notes_prepare_os_access notes_request_os_access notes_prepare_once notes_cancel_plan
notes_finalize_stopped_plan notes_prepare_grant notes_prepare_grant_replacement
notes_activate_grant notes_finalize_grant notes_revoke_grant notes_prepare_with_grant
reminders_prepare_os_access reminders_request_os_access reminders_prepare_once
reminders_cancel_plan reminders_finalize_stopped_plan reminders_prepare_grant
reminders_prepare_grant_replacement reminders_activate_grant reminders_finalize_grant
reminders_revoke_grant reminders_prepare_with_grant notes_prepare_observation_copy
notes_prepare_edit_recovery notes_accept_edit_recovery notes_finalize_edit_recovery
notes_abandon_edit_recovery notes_prepare_move_recovery notes_accept_move_recovery
notes_finalize_move_recovery notes_abandon_move_recovery notes_prepare_changes
notes_execute_approved notes_reconcile_created notes_prepare_folder_rename_correction
notes_reconcile_folder_rename_correction notes_reconcile_known_changes
notes_prepare_creation_recovery notes_execute_creation_recovery notes_finalize_creation_recovery
reminders_prepare_template_copy reminders_prepare_observation_copy reminders_prepare_ical_copy
reminders_prepare_changes reminders_execute_approved reminders_reconcile_lifecycle
reminders_prepare_observation_recovery reminders_accept_observation_recovery
reminders_finalize_observation_recovery reminders_abandon_observation_recovery
reminders_reconcile_created reminders_reconcile_known_changes reminders_prepare_update_correction
reminders_reconcile_correction policy_prepare_change policy_prepare_exception
policy_execute_approved policy_revoke_proposal
""".split())
SHARED = frozenset({"owner_request_review", "owner_review_status", "dav_execute_approved"})
CLIENT_MANAGEMENT = frozenset({"http_prepare_client", "http_execute_client", "http_client_status", "http_clients"})
KINDS = {"send": "mail", "trash": "mail", "cleanup_grant": "mail", "mail_recovery": "mail",
         "calendar_change": "calendar", "contact_change": "contacts"}


def validate_permissions(value):
    if (not isinstance(value, dict) or set(value) != set(SERVICES)
            or any(type(mode) is not str or mode not in {"none", "read", "full"} for mode in value.values())):
        raise SafetyError("Choose none, read or full access for each HTTP service")
    return {name: value[name] for name in SERVICES}


def service_for_tool(name):
    if name.startswith(("email_", "policy_")):
        return "mail"
    for service in SERVICES[1:]:
        if name.startswith(service + "_"):
            return service
    return None


def service_for_plan(plan):
    kind = plan.get("kind")
    if kind in KINDS:
        return KINDS[kind]
    if isinstance(kind, str):
        for service in ("notes", "reminders"):
            if kind.startswith(service + "_"):
                return service
    raise SafetyError("Unsupported HTTP plan scope")


class ClientPermissions(Middleware):
    """Authorize the exact invocation before any tool handler can run."""

    def __init__(self, store_factory=PlanStore, account=None):
        self.store_factory = store_factory
        self.account = account

    @staticmethod
    def _policy():
        identity = PRINCIPAL.get()
        if not isinstance(identity, dict):
            raise ToolError("HTTP client permission is unavailable")
        try:
            return validate_permissions(identity.get("permissions"))
        except SafetyError:
            raise ToolError("HTTP client permission is unavailable") from None

    @staticmethod
    def _manage_clients():
        identity = PRINCIPAL.get()
        return isinstance(identity, dict) and identity.get("manage_clients") is True

    def _plan_service(self, name, arguments):
        import os
        account = self.account if self.account is not None else os.environ.get("ICLOUD_MAIL_ACCOUNT", "")
        store = self.store_factory()
        plan_id = arguments.get("plan_id")
        if name.startswith("owner_") and arguments.get("scope", "service") == "managed-policy":
            managed = PlanStore(store.root / "managed-policy")
            binding = managed.read(managed.root / "binding.json")
            if not account or binding.get("account") != account:
                raise SafetyError("HTTP plan account mismatch")
            plan = managed.read(managed.path(plan_id, ".json"))
            if plan.get("kind") not in {"policy-change", "one-time-trash"}:
                raise SafetyError("HTTP plan kind mismatch")
            return "mail"
        plan = store.read(store.path(plan_id, ".json"))
        if not account or plan.get("payload", {}).get("account") != account:
            raise SafetyError("HTTP plan account mismatch")
        if plan.get("kind") == "http_client" and name.startswith(("owner_", "http_")):
            return "http-client-management"
        service = service_for_plan(plan)
        if name == "dav_execute_approved" and service not in {"calendar", "contacts"}:
            raise SafetyError("HTTP plan kind mismatch")
        if name == "email_plan_status" and service != "mail":
            raise SafetyError("HTTP plan kind mismatch")
        return service

    def allowed(self, name, policy, arguments=None, *, listing=False):
        if name in CLIENT_MANAGEMENT:
            return self._manage_clients()
        if name in SHARED:
            if listing:
                modes = [policy[s] for s in (("calendar", "contacts") if name == "dav_execute_approved" else SERVICES)]
                admin = self._manage_clients() and name != "dav_execute_approved"
                return (admin or any(mode == "full" for mode in modes)) if name != "owner_review_status" else (admin or any(mode != "none" for mode in modes))
            service = self._plan_service(name, arguments or {})
            if service == "http-client-management":
                return self._manage_clients()
            return policy[service] == "full" if name != "owner_review_status" else policy[service] != "none"
        if name not in READ_TOOLS and name not in WRITE_TOOLS:
            return False
        service = service_for_tool(name)
        if service is None or policy[service] == "none":
            return False
        if not listing and name == "email_plan_status":
            self._plan_service(name, arguments or {})
        return name in READ_TOOLS or policy[service] == "full"

    async def on_call_tool(self, context, call_next):
        try:
            permitted = self.allowed(context.message.name, self._policy(), context.message.arguments or {})
        except (SafetyError, OSError, ValueError, KeyError, TypeError):
            permitted = False
        if not permitted:
            raise ToolError("This HTTP client's approved permissions do not allow this tool or target")
        return await call_next(context)

    async def on_list_tools(self, context, call_next):
        policy = self._policy()
        return [tool for tool in await call_next(context) if self.allowed(tool.name, policy, listing=True)]

    async def on_read_resource(self, context, call_next):
        raise ToolError("HTTP resource access has no approved service classification")

    async def on_get_prompt(self, context, call_next):
        raise ToolError("HTTP prompt access has no approved service classification")

    async def on_list_resources(self, context, call_next):
        self._policy()
        return []

    async def on_list_resource_templates(self, context, call_next):
        self._policy()
        return []

    async def on_list_prompts(self, context, call_next):
        self._policy()
        return []
