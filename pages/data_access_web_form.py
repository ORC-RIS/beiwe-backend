from django.contrib import messages
from django.shortcuts import render
from django.views.decorators.http import require_GET
from markupsafe import Markup

from authentication.admin_authentication import (authenticate_researcher_login,
    get_researcher_allowed_studies, get_researcher_allowed_studies_as_query_set)
from constants.data_stream_constants import ALL_DATA_STREAMS
from database.data_access_models import PipelineUploadTags
from database.user_models import Researcher
from libs.internal_types import ResearcherRequest


@require_GET
@authenticate_researcher_login
def data_api_web_form_page(request: ResearcherRequest):
    warn_researcher_if_hasnt_yet_generated_access_key(request)
    return render(
        request,
        "data_api_web_form.html",
        context=dict(
            ALL_DATA_STREAMS=ALL_DATA_STREAMS,
            users_by_study=participants_by_study(request),
        )
    )


@require_GET
@authenticate_researcher_login
def pipeline_download_page(request: ResearcherRequest):
    warn_researcher_if_hasnt_yet_generated_access_key(request)
    # FIXME clean this up.
    # it is a bit obnoxious to get this data, we need to deduplcate it and then turn it back into a list
    tags_by_study = {
        study['id']: list(set(
            PipelineUploadTags.objects.filter(
                pipeline_upload__study__id=study['id']).values_list("tag", flat=True)
        ))
        for study in get_researcher_allowed_studies(request)
    }
    return render(
        request,
        "data_pipeline_web_form.html",
        context=dict(
            tags_by_study=tags_by_study,
            downloadable_studies=get_researcher_allowed_studies(request),
            users_by_study=participants_by_study(request),
        )
    )


def warn_researcher_if_hasnt_yet_generated_access_key(request: ResearcherRequest):
    if not request.session_researcher.access_key_id:
        msg = """You need to generate an <b>Access Key</b> and a <b>Secret Key </b> before you
        can download data. Go to <a href='/manage_credentials'> Manage Credentials</a> and click
        'Reset Data-Download API Access Credentials'. """
        messages.warning(request, Markup(msg))


def participants_by_study(request: ResearcherRequest):
    # dict of {study ids : list of user ids}
    return {
        study.pk: list(study.participants.order_by("patient_id").values_list("patient_id", flat=True))
        for study in get_researcher_allowed_studies_as_query_set(request)
    }
