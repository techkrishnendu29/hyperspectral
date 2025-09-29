from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
import ee
import datetime
import os
import json
import requests
from io import BytesIO

# ---------- Step 1: Authenticate Earth Engine ----------
serviceaccount = "gee-backend@ee-bairagisayan464.iam.gserviceaccount.com"
key_json = os.getenv("EE_KEY_JSON")

if key_json is None:
    raise Exception("Please set EE_KEY_JSON in Heroku Config Vars.")

key_dict = json.loads(key_json)

# Temporary file for EE credentials
with open("temp_key.json", "w") as f:
    json.dump(key_dict, f)

credentials = ee.ServiceAccountCredentials(serviceaccount, "temp_key.json")
ee.Initialize(credentials, project="ee-bairagisayan464")

# ---------- Step 2: FastAPI App ----------
app = FastAPI(title="NDVI & Multi-Layer API", description="Google Earth Engine NDVI, NDWI, SAVI, MSI, GNDVI API", version="1.1")

# ---------- Step 3: Helper functions ----------
def s2Mask(img):
    qa = img.select("QA60")
    mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
    return img.updateMask(mask)

def add_indices(img):
    """Add NDVI, NDWI, SAVI, MSI, GNDVI as bands to image"""
    try:
        ndvi = img.normalizedDifference(["B8", "B4"]).rename("NDVI")
    except:
        ndvi = None
    try:
        ndwi = img.normalizedDifference(["B3", "B8"]).rename("NDWI")
    except:
        ndwi = None
    try:
        savi = img.expression("((NIR - RED)/(NIR + RED + 0.5))*1.5", {
            "NIR": img.select("B8"),
            "RED": img.select("B4")
        }).rename("SAVI")
    except:
        savi = None
    try:
        msi = img.expression("SWIR / NIR", {
            "SWIR": img.select("B11"),
            "NIR": img.select("B8")
        }).rename("MSI")
    except:
        msi = None
    try:
        gndvi = img.normalizedDifference(["B8", "B3"]).rename("GNDVI")
    except:
        gndvi = None

    bands = [b for b in [ndvi, ndwi, savi, msi, gndvi] if b is not None]
    return img.addBands(ee.Image.cat(bands)).copyProperties(img, ["system:time_start"])

# ---------- Step 4: API Endpoint ----------
@app.get("/multi_index_map")
def get_multi_index_map(
    state: str = Query(..., description="State Name"),
    district: str = Query(..., description="District Name"),
    location: str = Query(..., description="Location Name"),
):
    try:
        # Dates
        end = datetime.date.today()
        start = end - datetime.timedelta(days=30)

        # Location filters
        country = ee.FeatureCollection("projects/ee-bairagisayan464/assets/GADM-IND").filter(
            ee.Filter.eq("COUNTRY", "India")
        )
        state_fc = country.filter(ee.Filter.eq("NAME_1", state))
        district_fc = state_fc.filter(ee.Filter.eq("NAME_2", district))
        location_fc = district_fc.filter(ee.Filter.eq("NAME_3", location))

        if location_fc.size().getInfo() == 0:
            raise HTTPException(status_code=404, detail="Location not found in GEE assets")

        # Image collection
        imgc = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterDate(str(start), str(end))
            .filterBounds(location_fc)
            .map(s2Mask)
            .map(add_indices)
        )

        if imgc.size().getInfo() == 0:
            raise HTTPException(status_code=404, detail="No Sentinel-2 images found for this location and date range")

        # Median composite
        image = imgc.median().clip(location_fc)

        # Prepare visualization parameters for each index
        vis_params = {
            "NDVI": {"min":0,"max":1,"palette":["FFFFFF","CE7E45","DF923D","F1B555","FCD163","99B718","74A901","66A000","529400","3E8601","207401","056201","004C00","023B01","012E01","011D01","011301"]},
            "NDWI": {"min":-1,"max":1,"palette":["brown","blue"]},
            "SAVI": {"min":0,"max":1,"palette":["white","orange","darkgreen"]},
            "MSI": {"min":0,"max":2,"palette":["white","yellow","red"]},
            "GNDVI": {"min":0,"max":1,"palette":["white","lightblue","darkgreen"]}
        }

        # Select layer parameter
        index_order = ["NDVI","NDWI","SAVI","MSI","GNDVI"]

        # Generate images and stream as zip if requested
        # For simplicity, return first index as streaming image for demo
        # User can call /multi_index_map?index=NDVI etc. in future
        first_index = index_order[0]
        url = image.select(first_index).getThumbURL({
            "region": location_fc.geometry(),
            "scale": 30,
            "format": "png",
            **vis_params[first_index]
        })

        img_response = requests.get(url, stream=True)
        if img_response.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Failed to fetch {first_index} image from GEE")

        return StreamingResponse(BytesIO(img_response.content), media_type="image/png")

    except ee.EEException as e:
        raise HTTPException(status_code=500, detail=f"Earth Engine error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
