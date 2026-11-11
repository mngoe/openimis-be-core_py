from core.services.base import BaseService
from core.services.userServices import create_or_update_interactive_user, create_or_update_user_roles, \
    create_or_update_user_districts, create_or_update_officer_villages, create_or_update_officer, \
    create_or_update_claim_admin, create_or_update_core_user, change_user_password, set_user_password, \
    reset_user_password, user_authentication


def wait_for_mutation(client_mutation_id, attempts=10, delay=0.3):
    """
    Wait (a few seconds at most) until the mutation `client_mutation_id` is no longer in
    the RECEIVED state, so a query following it sees its result. Ported from openIMIS
    core (needed by the modules' develop code); the wait re-reads the log.
    """
    import time
    from core.models import MutationLog
    mutation = MutationLog.objects.filter(client_mutation_id=client_mutation_id).first()
    if not mutation:
        return
    for _ in range(attempts):
        if mutation.status != MutationLog.RECEIVED:
            return
        time.sleep(delay)
        mutation.refresh_from_db()
