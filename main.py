from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
import ee
import datetime
import os
import json

# ---------------- Earth Engine Authentication ----------------
SERVICE_ACCOUNT = "gee-backend@ee-bairagisayan464.iam.gserviceaccount.com"

# Load Service Account Key from Render Config Var
key_json = os.getenv("EE_KEY_JSON")
if not key_json:
    raise Exception("Please set EE_KEY_JSON in Render Config Vars as JSON string of your service account key")

key_dict = json.loads(key_json)

# Save temp key file
with open("temp_key.json", "w") as f:
    json.dump(key_dict, f)

# Initialize Earth Engine
credentials = ee.ServiceAccountCredentials(SERVICE_ACCOUNT, "temp_key.json")
ee.Initialize(credentials, project="ee-bairagisayan464")

# ---------------- FastAPI App ----------------
app = FastAPI(title="Satellite Multi-layer API", version="3.0")

# ---------------- Cloud Mask ----------------
def s2Mask(img):
    qa = img.select("QA60")
    mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
    return img.updateMask(mask)

# ---------------- Layer Generator ----------------
def generate_layers(image):
    layers = {}

    try:
        layers["true_color"] = image.getThumbURL({
            "bands": ["B4", "B3", "B2"], "min": 0, "max": 3000, "format": "png", "scale": 10
        })
    except Exception:
        pass

    try:
        layers["false_color"] = image.getThumbURL({
            "bands": ["B8", "B4", "B3"], "min": 0, "max": 3000, "format": "png", "scale": 10
        })
    except Exception:
        pass

    try:
        layers["swir"] = image.getThumbURL({
            "bands": ["B12", "B8", "B4"], "min": 0, "max": 3000, "format": "png", "scale": 10
        })
    except Exception:
        pass

    try:
        ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")
        layers["ndvi"] = ndvi.getThumbURL({
            "min": 0, "max": 1, "palette": ["white", "lightgreen", "green", "darkgreen"],
            "format": "png", "scale": 10
        })
    except Exception:
        pass

    try:
        ndwi = image.normalizedDifference(["B3", "B8"]).rename("NDWI")
        layers["ndwi"] = ndwi.getThumbURL({
            "min": -1, "max": 1, "palette": ["brown", "blue"],
            "format": "png", "scale": 10
        })
    except Exception:
        pass

    try:
        gndvi = image.normalizedDifference(["B8", "B3"]).rename("GNDVI")
        layers["gndvi"] = gndvi.getThumbURL({
            "min": 0, "max": 1, "palette": ["white", "lightblue", "darkgreen"],
            "format": "png", "scale": 10
        })
    except Exception:
        pass

    try:
        ci_green = image.expression(
            "(NIR / GREEN) - 1",
            {"NIR": image.select("B8"), "GREEN": image.select("B3")}
        ).rename("CI_green")
        layers["ci_green"] = ci_green.getThumbURL({
            "min": 0, "max": 3, "palette": ["white", "pink", "darkred"],
            "format": "png", "scale": 10
        })
    except Exception:
        pass

    try:
        ci_rededge = image.expression(
            "(NIR / RE) - 1",
            {"NIR": image.select("B8"), "RE": image.select("B5")}
        ).rename("CI_red_edge")
        layers["ci_rededge"] = ci_rededge.getThumbURL({
            "min": 0, "max": 3, "palette": ["white", "lightyellow", "darkred"],
            "format": "png", "scale": 10
        })
    except Exception:
        pass

    return layers

# ---------------- API Endpoint ----------------
@app.get("/multi_layer_map")
def multi_layer_map(
    state: str = Query(..., description="State Name"),
    location: str = Query(None, description="District/City (Optional)"),
    start_date: str = Query(..., description="Start Date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End Date (YYYY-MM-DD)")
):
    try:
        # Validate dates
        try:
            start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
            end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
        except:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

        # Load Feature Collection
        fc = ee.FeatureCollection("projects/ee-bairagisayan464/assets/GADM-IND")
        region = fc.filter(ee.Filter.eq("NAME_1", state))

        if location and location.strip() != "":
            region = region.filter(ee.Filter.eq("NAME_3", location))

        if region.size().getInfo() == 0:
            raise HTTPException(status_code=404, detail="Region not found in GEE assets")

        # Sentinel-2 Collection
        collection = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                      .filterDate(str(start), str(end))
                      .filterBounds(region)
                      .map(s2Mask))

        if collection.size().getInfo() == 0:
            raise HTTPException(status_code=404, detail="No images found for this region/date range")

        # Composite Image
        image = collection.median().clip(region)

        # Generate Layers
        layers = generate_layers(image)

        return JSONResponse(content=layers)

    except ee.EEException as e:
        raise HTTPException(status_code=500, detail=f"Earth Engine error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

