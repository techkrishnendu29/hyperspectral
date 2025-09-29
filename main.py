from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
import ee
import datetime
import os
import json
import requests
from io import BytesIO
import base64

# ------------------ Earth Engine Authentication ------------------
SERVICE_ACCOUNT = "gee-backend@ee-bairagisayan464.iam.gserviceaccount.com"
key_json = os.getenv("EE_KEY_JSON")

if not key_json:
    raise Exception("Please set EE_KEY_JSON in Render Config Vars.")

key_dict = json.loads(key_json)

with open("temp_key.json", "w") as f:
    json.dump(key_dict, f)

credentials = ee.ServiceAccountCredentials(SERVICE_ACCOUNT, "temp_key.json")
ee.Initialize(credentials, project="ee-bairagisayan464")

# ------------------ FastAPI App ------------------
app = FastAPI(title="Hyperspectral API", version="1.0")

# ------------------ Cloud Mask ------------------
def s2Mask(img):
    qa = img.select("QA60")
    mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
    return img.updateMask(mask)

# ------------------ Layer Generation ------------------
def generate_layers(image):
    layers = {}
    try:
        ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")
        layers["NDVI"] = ndvi
    except: pass

    try:
        ndwi = image.normalizedDifference(["B3", "B8"]).rename("NDWI")
        layers["NDWI"] = ndwi
    except: pass

    try:
        savi = image.expression('((NIR-RED)/(NIR+RED+0.5))*1.5', {
            'NIR': image.select("B8"), 'RED': image.select("B4")
        }).rename("SAVI")
        layers["SAVI"] = savi
    except: pass

    try:
        msi = image.expression('B11/B8', {
            'B11': image.select("B11"), 'B8': image.select("B8")
        }).rename("MSI")
        layers["MSI"] = msi
    except: pass

    try:
        gndvi = image.normalizedDifference(["B8", "B3"]).rename("GNDVI")
        layers["GNDVI"] = gndvi
    except: pass

    return layers

# ------------------ Convert Image to Base64 ------------------
def ee_image_to_base64(img, region):
    url = img.getThumbURL({
        "region": region,
        "scale": 30,
        "format": "png"
    })
    resp = requests.get(url)
    if resp.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch image from GEE")
    return base64.b64encode(resp.content).decode("utf-8")

# ------------------ API Endpoint ------------------
@app.get("/multi_layer_map")
def multi_layer_map(
    state: str = Query(..., description="State Name"),
    location: str = Query(None, description="District/City (Optional)"),
    start_date: str = Query(..., description="Start Date YYYY-MM-DD"),
    end_date: str = Query(..., description="End Date YYYY-MM-DD")
):
    try:
        start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()

        fc = ee.FeatureCollection("projects/ee-bairagisayan464/assets/GADM-IND")
        region = fc.filter(ee.Filter.eq("NAME_1", state))
        if location and location.strip() != "":
            region = region.filter(ee.Filter.eq("NAME_3", location))

        if region.size().getInfo() == 0:
            raise HTTPException(status_code=404, detail="Region not found")

        collection = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                      .filterDate(str(start), str(end))
                      .filterBounds(region)
                      .map(s2Mask))

        if collection.size().getInfo() == 0:
            raise HTTPException(status_code=404, detail="No images found for selected region/date")

        image = collection.median().clip(region)

        layers_dict = generate_layers(image)

        # Convert to base64
        result = {}
        for name, layer in layers_dict.items():
            try:
                result[name] = ee_image_to_base64(layer, region.geometry())
            except:
                continue

        return JSONResponse(content=result)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
