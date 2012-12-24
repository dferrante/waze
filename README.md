Installation Requirements
=========================

pip install simplekml requests Gnosis-Utils numpy
sudo apt-get install gdal-bin

Running
=======
python exportdrives.py

Notes
=====
this script will export the last month or so of drives directly from waze's servers.  waze only keeps about a month available,
so you'll have to keep running the script at least once a month to be able to get all your drives.  if you just keep all the files in the same
directory, the script will skip drives you've already downloaded.

in the top of the file is:
    kmlfolderrules = [
        ('morning', lambda x: x['startdate'].weekday() < 5 and x['startdate'].hour >= 8 and x['startdate'].hour <= 10),
        ('evening', lambda x: x['startdate'].weekday() < 5 and x['startdate'].hour >= 17 and x['startdate'].hour <= 19),
        ('other', lambda x: True),
    ]

you can edit these lambdas to have your commute times be represented and organized in the KML.  the code above will have a 'morning'
folder for starttimes on M-F between 8AM and 10:59AM, and 'evening' for M-F between 5PM and 7:59PM

enjoy!