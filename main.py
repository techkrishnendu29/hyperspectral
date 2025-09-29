from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import ee
import datetime
import os
import json
import requests
from io import BytesIO

# ---------------- Earth Engine Authentication ----------------
serviceaccount = "gee-backend@ee-bairagisayan464.iam.gserviceaccount.com"
key_json = os.getenv("EE_KEY_JSON")

if key_json is None:
    raise Exception("Please set EE_KEY_JSON in Render Config Vars as EE_KEY_JSON")

key_dict = json.loads(key_json)

# Temporary file for credentials
with open("temp_key.json", "w") as f:
    json.dump(key_dict, f)

credentials = ee.ServiceAccountCredentials(serviceaccount, "temp_key.json")
ee.Initialize(credentials, project="ee-bairagisayan464")

# ---------------- FastAPI App ----------------
app = FastAPI(title="Multi-layer Satellite Image API", version="1.0")

# ---------------- Cloud Mask Function ----------------
def s2Mask(img):
    qa = img.select('QA60')
    mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
    return img.updateMask(mask)

# ---------------- Layer Generation Function ----------------
def generate_layers(image):
    layers = {}

    # True Color
  #  layers['true_color'] = image.getThumbURL({'bands':['B4','B3','B2'], 'min':0, 'max':3000, 'format':'png', 'scale':10})
    # False Color
   # layers['false_color'] = image.getThumbURL({'bands':['B8','B4','B3'], 'min':0, 'max':3000, 'format':'png', 'scale':10})
    # SWIR
   # layers['swir'] = image.getThumbURL({'bands':['B12','B8','B4'], 'min':0, 'max':3000, 'format':'png', 'scale':10})
    # NDVI
    ndvi = image.normalizedDifference(['B8','B4']).rename('NDVI')
    layers['ndvi'] = ndvi.getThumbURL({'min':0,'max':1,'palette':['white','lightgreen','green','darkgreen'],'format':'png','scale':10})
    # NDWI
    ndwi = image.normalizedDifference(['B3','B8']).rename('NDWI')
    layers['ndwi'] = ndwi.getThumbURL({'min':-1,'max':1,'palette':['brown','blue'],'format':'png','scale':10})
    # EVI
    evi = image.expression('2.5 * ((NIR - RED) / (NIR + 6*RED - 7.5*BLUE + 1))',
                           {'NIR': image.select('B8'), 'RED': image.select('B4'), 'BLUE': image.select('B2')}).rename('EVI')
    layers['evi'] = evi.getThumbURL({'min':0,'max':1,'palette':['white','yellow','green'],'format':'png','scale':10})
    # SAVI
    savi = image.expression('((NIR - RED) / (NIR + RED + 0.5)) * 1.5',
                            {'NIR': image.select('B8'),'RED': image.select('B4')}).rename('SAVI')
    layers['savi'] = savi.getThumbURL({'min':0,'max':1,'palette':['white','orange','darkgreen'],'format':'png','scale':10})
    # GNDVI
    gndvi = image.normalizedDifference(['B8','B3']).rename('GNDVI')
    layers['gndvi'] = gndvi.getThumbURL({'min':0,'max':1,'palette':['white','lightblue','darkgreen'],'format':'png','scale':10})
    # CI_green
    ci_green = image.expression('(NIR / GREEN) - 1',{'NIR': image.select('B8'),'GREEN': image.select('B3')}).rename('CI_green')
    layers['ci_green'] = ci_green.getThumbURL({'min':0,'max':3,'palette':['white','pink','darkred'],'format':'png','scale':10})
    # CI_red_edge
    ci_rededge = image.expression('(NIR / RE) - 1',{'NIR': image.select('B8'),'RE': image.select('B5')}).rename('CI_red_edge')
    layers['ci_rededge'] = ci_rededge.getThumbURL({'min':0,'max':3,'palette':['white','lightyellow','darkred'],'format':'png','scale':10})

    return layers

# ---------------- API Endpoint ----------------
@app.get("/multi_layer_map")
def multi_layer_map(
    state: str = Query(..., description="State Name"),
    location: str = Query(None, description="Location/Sub-district/City (Optional)"),
    start_date: str = Query(..., description="Start Date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End Date (YYYY-MM-DD)")
):
    try:
        # Validate dates
        try:
            start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
            end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
        except:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

        # Load GADM FeatureCollection
        fc = ee.FeatureCollection("projects/ee-bairagisayan464/assets/GADM-IND")
        region = fc.filter(ee.Filter.eq("NAME_1", state))

        if location and location.strip() != "":
            region = region.filter(ee.Filter.eq("NAME_3", location))

        if region.size().getInfo() == 0:
            raise HTTPException(status_code=404, detail="Region not found in GEE assets")

        # Cloud-masked Sentinel-2 Collection
        collection = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                      .filterDate(str(start), str(end))
                      .filterBounds(region)
                      .map(s2Mask))

        if collection.size().getInfo() == 0:
            raise HTTPException(status_code=404, detail="No images found for the selected region/date range")

        image = collection.median().clip(region)

        # Generate layer URLs
        layers = generate_layers(image)

        return JSONResponse(content=layers)

    except ee.EEException as e:

        raise HTTPException(status_code=500, detail=f"Earth Engine error: {e}")
