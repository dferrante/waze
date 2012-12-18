import os, simplekml
from gnosis.xml.objectify import make_instance

outfile = 'drives.kml'

def colorspeed(speed):
    if speed < 30:
        argb = (255,125*(speed/30.0)+130,0,0)
    elif speed >= 30 and speed < 65:
        argb = (255,0,125*((speed-30)/45.0)+130,0)
    else:
        argb = (255,0,0,125*((speed-65)/20.0)+130)
    color = '%02x%02x%02x%02x' % argb
    return color

def datadict(data):
    d = {}
    for item in data:
        d[item.name] = item.PCDATA
    return d

def run():
    kmlfiles = sorted([x for x in os.listdir('.') if '.kml' in x and x != outfile])

    kml = simplekml.Kml()
    for kmlfile in kmlfiles:
        folder = kml.newfolder(name=kmlfile[:-4])
        kmldata = make_instance(open(kmlfile).read())
        try:
            lines = kmldata.Document.Folder.Placemark
        except:
            continue
        for l in lines:
            try:
                data = datadict(l.ExtendedData.SchemaData.SimpleData)
            except:
                continue

            status = data['status']
            if status != 'OK':
                continue
            name = data['Name'].strip(',') if 'Name' in data else ''
            length = int(int(data['length'])*0.000621371)
            speed = int(int(data['speed'])*0.621371)
            coords = [tuple(x.split(',')) for x in l.LineString.coordinates.PCDATA.split()]

            lin = folder.newlinestring(coords=coords, name='%s - %smi - %smph' % (name, length, speed))
            lin.style.linestyle.width = 4
            lin.style.linestyle.color = colorspeed(speed)
            lin.tessellate = 1
    kml.save(outfile)


if __name__ == '__main__':
    run()