#!/usr/bin/env python

import logging
import csv
import codecs
import requests
from fastapi import FastAPI, UploadFile
from fastapi.responses import JSONResponse

# Config
APP_NAME = 'VERO Python Task Server'
APP_VERSION = '1'
BAUBUDDY_LOGIN_ENDPOINT = 'https://api.baubuddy.de/index.php/login'
BAUBUDDY_ACTIVE_ENDPOINT = 'https://api.baubuddy.de/dev/index.php/v1/vehicles/select/active'
BAUBUDDY_LABELS_ENDPOINT = 'https://api.baubuddy.de/dev/index.php/v1/labels/'
KEY_KURZNAME = 'kurzname'
KEY_LABELIDS = 'labelIds'
KEY_HU = 'hu'
KEY_COLORCODE = 'colorCode'
KEY_LABELCOLOR = '_labelColor'

app = FastAPI()

def loginBaubuddy():
    headers = {
        "Authorization": "Basic QVBJX0V4cGxvcmVyOjEyMzQ1NmlzQUxhbWVQYXNz",
        "Content-Type": "application/json"
    }
    payload = {
        "username": "365",
        "password": "1"
    }
    try:
        response = requests.post(BAUBUDDY_LOGIN_ENDPOINT, json=payload, headers=headers)
    except Exception as err:
        logging.error(f'Baubuddy login call failed unexpectedly: {err=}')
        return ''
    if response.status_code != 200:
        logging.error(f'Baubuddy login call failed with status: {response.status_code}')
        return ''

    return response.json()["oauth"]["access_token"]

def getBaubuddyActiveVehicles(token):
    headers = {
        "Authorization": f"Bearer {token}",
    }
    try:
        response = requests.get(BAUBUDDY_ACTIVE_ENDPOINT, headers=headers)
    except Exception as err:
        logging.error(f'Baubuddy vehicles call failed unexpectedly: {err=}')
        return []
    if response.status_code != 200:
        logging.error(f'Baubuddy vehicles call failed with status: {response.status_code}')
        return []

    return response.json()

def getBaubuddyLabelColor(token, label):
    headers = {
        "Authorization": f"Bearer {token}",
    }
    try:
        response = requests.get(BAUBUDDY_LABELS_ENDPOINT + label, headers=headers)
    except Exception as err:
        logging.error(f'Baubuddy label call failed unexpectedly: {err=}')
        return ''
    if response.status_code != 200:
        logging.error(f'Baubuddy label call failed with status: {response.status_code}')
        return ''

    labelData = response.json()[0] # the result is a list with element for this labelId
    if KEY_COLORCODE not in labelData:
        return ''
    
    return labelData[KEY_COLORCODE]

@app.get("/")
async def root():
    return {"name": APP_NAME, "version": APP_VERSION}

@app.post("/vehicles")
async def vehicles(file: UploadFile | None = None):
    logging.info("Vehicles request received.")

    # Request sanity checks
    if not file:
        logging.error("Failed to process vehicles. No CSV file provided.")
        return JSONResponse(status_code=400, content={"message": "No CSV file provided", "vehicles": []})

    # Parsing vehicles from client CSV file    
    vehicles = []
    try:
        csvReader = csv.DictReader(codecs.iterdecode(file.file, 'utf-8'), delimiter=';')
        for row in csvReader:
            vehicles.append(row)
    except Exception as err:
        logging.error(f'Failed to process vehicles. Unexpected error in processing CSV file. Error: {err=}')
        return JSONResponse(status_code=400, content={'message': 'Failed to process CSV file', 'vehicles': []})
    if not vehicles:
        logging.error('Failed to process vehicles. No vehicles found in provided CSV file.')
        return JSONResponse(status_code=400, content={'message': 'No vehicles found in provided CSV file.', 'vehicles': []})
    
    logging.info(f'CSV vehicles parsed: {len(vehicles)}')

    # Login to Baubuddy server
    baubuddyToken = loginBaubuddy()
    if not baubuddyToken:
        logging.error('Failed to process vehicles. Unable to login to Baubuddy server.')
        return JSONResponse(status_code=503, content={'message': 'Failed to login to Baubuddy server', 'vehicles': []})

    logging.info('Baubuddy login success.')

    # Load active vehicles data from Baubuddy server
    activeVehiclesList = getBaubuddyActiveVehicles(baubuddyToken)
    if not activeVehiclesList:
        logging.error('Failed to process vehicles. Unable to obtain active vehicles data from Baubuddy server.')
        return JSONResponse(status_code=503, content={'message': 'Failed to obtain active vehicles data from Baubuddy server', 'vehicles': []})
    
    # make active vehicles dictionary for optimal search
    activeVehiclesDict = {}
    for v in activeVehiclesList:
        activeVehiclesDict[v[KEY_KURZNAME]] = v

    logging.info(f'Baubuddy active vehicles loaded: {len(activeVehiclesList)}')

    # Update vehicles with active vehicles data
    # NOTE: ASSUMPTION: 'kurzname' is unique identifier of vehicles
    for v in vehicles:
        if v[KEY_KURZNAME] not in activeVehiclesDict:
            logging.warning(f'Skipping vehicle {v[KEY_KURZNAME]} as it does not exist in active vehicles')
            # NOTE: as 'hu' key is not added here, this vehicle will be removed by the filter below
            continue
        av = activeVehiclesDict[v[KEY_KURZNAME]]
        for key in av:
            # NOTE: ASSUMPTION: 
            #       (VARIANT 1) Active data is more recent and should overwrite the CSV values. 
            #       (VARIANT 2) CSV to be considered user-forced data that should prevail. Active vehicles data fills all missing or empty keys
            #       Both variants implemented. Here is used VARIANT 2 for the ease of testing, for example setting various labelIds, etc.
            #
            # VARIANT 1:
            # add or overwrite CSV data with active vehicles data. 
            # - avoid direct vehicle assignment here, as there (possibly) might be additional (non existent in active data) keys in client vehicle CSV, 
            #   which the user might want to use in Excel (pass with -k)
            #v[key] = av[key]
            #
            # VARIANT 2:
            if ((key not in v) or (not v[key])): # add active vehicles data when CSV has no value for this key
               v[key] = av[key]

    # Filter out vehicles which has no 'hu' (including these skipped above) or its value is empty in the active data (by specification)
    vehicles = list(filter(lambda x: ((KEY_HU in x) and (x[KEY_HU])), vehicles))

    logging.info(f'Active vehicles after filtering: {len(vehicles)}')

    # Parse label IDs of vehicles and read label color codes from Baubuddy server
    labelColors = {} # cache for label colors
    for v in vehicles:
        if v[KEY_LABELIDS]:
            for id in v[KEY_LABELIDS].strip().split(','):
                if id not in labelColors:
                    labelColors[id] = getBaubuddyLabelColor(baubuddyToken, id)
                v[KEY_LABELCOLOR] = labelColors[id] # an additional key added to store the first labelIds color
                break # NOTE: by specification only the first labelID color code should be used

    logging.info('Vehicles processing completed.')

    return JSONResponse(status_code=200, content={'message': 'OK', 'vehicles': vehicles})

if __name__ == '__main__':
    import uvicorn
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    logging.info(f'{APP_NAME} ({APP_VERSION})')
    uvicorn.run(app, host='127.0.0.1', port=8000)
