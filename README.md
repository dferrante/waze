this script will export the last month or so of drives directly from waze's servers.

it needs the following installed already:
python-requests
ogr2ogr (in the gdal-bin package in ubuntu)

put the script where you want to keep your kml files.  the files coming from waze are GML formatted and my script deletes them but thats easy enough to change.  
once you run the script, if you keep those files in the directory, it will skip grabbing and converting them the next time around.  the GML is converted to KML via ogr2ogr, but if you want some other format that ogr2ogr supports, its easy enough to change (the -f arg).

enjoy!