from __future__ import absolute_import, unicode_literals

import json
import logging

from celery import shared_task
from core.models import MutationLog
from django.utils import translation

logger = logging.getLogger(__name__)


@shared_task
def openimis_mutation_async(mutation_id, module, class_name):
    """
    This method is called by the OpenIMISMutation, directly or asynchronously to call the async_mutate method.
    :param mutation_id: ID of the mutation object. We're not passing the whole object because an async call would have
                        to serialize it into the queue.
    :param module: "claim", "insuree"...
    :param class_name: Name of the OpenIMISMutation class whose async_mutate() will be called
    :return: unused, returns "OK"
    """
    mutation = None
    try:
        mutation = MutationLog.objects.get(id=mutation_id)
        # __import__ needs to import the module with .schema to force .schema to load, then .schema.TheRealMutation
        mutation_class = getattr(__import__(f"{module}.schema").schema, class_name)

        if mutation.user and mutation.user.language:
            translation.activate(mutation.user.language)
        result = mutation_class.async_mutate(mutation.user, **json.loads(mutation.json_content))

        if result:
            mutation.mark_as_failed(result)
        else:
            mutation.mark_as_successful()
        return "OK"
    except Exception as exc:
        if mutation:
            mutation.mark_as_failed(str(exc))
        logger.warning(f"Exception while processing mutation id {mutation_id}")
        raise exc
