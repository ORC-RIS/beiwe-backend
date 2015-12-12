from bson import ObjectId
from datetime import datetime
from flask import Blueprint, request, abort, json
from multiprocessing.pool import ThreadPool
from StringIO import StringIO
from zipfile import ZipFile, ZIP_DEFLATED

from bson.errors import InvalidId
from db.data_access_models import ChunksRegistry
from db.study_models import Study
from db.user_models import Admin, User
from libs.s3 import s3_retrieve
from config.constants import (API_TIME_FORMAT, VOICE_RECORDING, ALL_DATA_STREAMS,
                              CONCURRENT_NETWORK_OPS)
from boto.utils import JSONDecodeError
from flask.helpers import send_file
from _io import BytesIO

# Data Notes
# The call log has the timestamp column as the 3rd column instead of the first.
# The Wifi and Identifiers have timestamp in the file name.
# The debug log has many lines without timestamps.

data_access_api = Blueprint('data_access_api', __name__)

@data_access_api.route("/get-data/v1", methods=['POST', "GET"])
def grab_data():
    """ Required: access key, access secret, study_id
    JSON blobs: data streams, users - default to all
    Strings: date-start, date-end - format as "YYYY-MM-DDThh:mm:ss" 
    optional: top-up = a file (registry.dat)
    cases handled: 
        missing creds or study, invalid admin or study, admin does not have access
        admin creds are invalid 
        (Flask automatically returns a 400 response if a parameter is accessed
        but does not exist in request.values() )
    Returns a zip file of all data files found by the query. """
    #Case: bad study id
    try: study_id = ObjectId(request.values["study_id"])
    except InvalidId: study_id = None
    study_obj = Study(study_id)
    if not study_obj: abort(404)
    #Cases: invalid access creds
    access_key = request.values["access_key"]
    access_secret = request.values["secret_key"]
    admin = Admin(access_key_id=access_key)
    if not admin: abort(403) #access key DNE
    if admin._id not in study_obj['admins']:
        abort(403) #admin is not credentialed for this study
    if not admin.validate_access_credentials(access_secret):
        abort(403) #incorrect secret key
    query = {}
    #select data streams
    if 'data_streams' in request.values: #note: researchers use the term "data streams" instead of "data types"
        try: query['data_types'] = json.loads(request.values['data_streams'])
        except JSONDecodeError: query['data_types'] = request.form.getlist('data_streams')
        for data_stream in query['data_types']:
            if data_stream not in ALL_DATA_STREAMS: abort(404)
    #select users
    if 'user_ids' in request.values:
        try: query['user_ids'] = [user for user in json.loads(request.values['user_ids'])]
        except JSONDecodeError: query['user_ids'] = request.form.getlist('user_ids')
        for user_id in query['user_ids']: #Case: one of the user ids was invalid
            if not User(user_id): abort(404)
    #construct time ranges
    if 'time_start' in request.values: query['start'] = str_to_datetime(request.values['time_start'])
    if 'time_end' in request.values: query['end'] = str_to_datetime(request.values['time_end'])
    registry = {}
    if "registry" in request.values:
        registry = parse_registry(request.values["registry"]) 
    #Do Query
    chunks = ChunksRegistry.get_chunks_time_range(study_id, **query)
    get_these_files = []
    for chunk in chunks:
        if (chunk['chunk_path'] in registry and
            registry[chunk['chunk_path']] == chunk["chunk_hash"]): continue
        get_these_files.append(chunk)
    #Retrieve data
    pool = ThreadPool(CONCURRENT_NETWORK_OPS)
    chunks_and_content = pool.map(batch_retrieve_for_api_request, get_these_files, chunksize=1) 
    #write data to zip.  If the request comes from the web form we need to use
    # a bytesio "file" object to return a file blob, if it came from the command
    # line we use a StringIO because that was how it was written.  :D
    if 'web_form' in request.values: f = BytesIO()
    else: f = StringIO()
    z = ZipFile(f, mode="w", compression=ZIP_DEFLATED)
    ret_reg = {}
    for chunk, file_contents in chunks_and_content:
        file_name = ( ("%s/%s/%s.csv" if chunk['data_type'] != VOICE_RECORDING else "%s/%s/%s.mp4")
                      % (chunk["user_id"], chunk["data_type"],
                         str(chunk["time_bin"]).replace(":", "_") ) )
        ret_reg[chunk['chunk_path']] = chunk["chunk_hash"]
        z.writestr(file_name, file_contents)
    if 'web_form' not in request.values:
        z.writestr("registry", json.dumps(ret_reg)) #and add the registry file.
    z.close()
    if 'web_form' in request.values:
        f.seek(0)
        return send_file(f, attachment_filename="data.zip",mimetype="zip",as_attachment=True)
    return f.getvalue()

def parse_registry(reg_dat):
    """ Parses the provided registry.dat file and returns a dictionary of chunk
    file names and hashes.  The registry file is a json dictionary containing a
    list of file names and hashes""" 
    return json.loads(reg_dat)

""" Time Handling """
def str_to_datetime(time_string):
    try: return datetime.strptime(time_string, API_TIME_FORMAT)
    except ValueError as e:
        #TODO: document this error (for mat) or change this error 
        if "does not match format" in e.message: abort(400)

def batch_retrieve_for_api_request(chunk):
    print chunk['chunk_path']
    return chunk, s3_retrieve(chunk["chunk_path"], chunk["study_id"], raw_path=True)
