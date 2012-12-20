import os, simplekml, datetime
from gnosis.xml.objectify import make_instance

outfile = 'drives.kml'

def colorspeed(speed):
    alpha = 200
    speed = speed-10
    maxspeed = 90.0
    midpoint = maxspeed/2.0
    limiter = lambda x: 255 if x > 255 else 0 if x < 0 else int(x)

    argb = (
        alpha,
        0 if speed <= midpoint else 255*((speed-midpoint)/midpoint),
        255*(speed/midpoint) if speed <= midpoint else 255*(1-((speed-midpoint)/midpoint)),
        255*(1-(speed/midpoint)) if speed <= midpoint else 0,
    )
    argb = tuple(map(limiter, argb))
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
    morningfolder = kml.newfolder(name='morning commute')
    eveningfolder = kml.newfolder(name='evening commute')
    otherfolder = kml.newfolder(name='other')
    for kmlfile in kmlfiles:
        sd = map(int,kmlfile[:-4].split('-')[:3])
        st = map(int,kmlfile[:-4].split('-')[3].split(':'))
        ed = map(int,kmlfile[:-4].split('-')[4:7])
        et = map(int,kmlfile[:-4].split('-')[7].split(':'))
        distance = kmlfile[:-4].split('-')[-1]
        startdate = datetime.datetime(2000+sd[0], sd[1], sd[2], st[0], st[1])
        enddate = datetime.datetime(2000+ed[0], ed[1], ed[2], et[0], et[1])
        subfolder = otherfolder
        if startdate.weekday() < 5:
            if startdate.hour >= 8 and startdate.hour <= 10:
                subfolder = morningfolder
            elif startdate.hour >= 17 and startdate.hour <= 19:
                subfolder = eveningfolder

        if distance == 0:
            continue

        folder = subfolder.newfolder(name='%s %s' % (startdate.strftime('%m/%d/%y %H:%M'), distance))
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
            name = data['Name'].strip(',') if 'Name' in data and data['Name'] else ''
            length = int(int(data['length'])*0.000621371)
            speed = int(int(data['speed'])*0.621371)
            coords = [tuple(x.split(',')) for x in l.LineString.coordinates.PCDATA.split()]

            if speed > 120:
                continue

            lin = folder.newlinestring(coords=coords, name='%s - %smi - %smph' % (name, length, speed))
            lin.style.linestyle.width = 8
            lin.style.linestyle.color = colorspeed(speed)
            lin.tessellate = 1
    kml.save(outfile)


if __name__ == '__main__':
    run()