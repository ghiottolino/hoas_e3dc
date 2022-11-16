
import json

from .const import DOMAIN


USERNAME = 'USERNAME_HERE'
PASS = 'PASSWORD_HERE'
SERIALNUMBER = 'SERIAL_NUMER'
CONFIG = {"powermeters": [{"index": 6}]}

print("web connection")
e3dc = E3DC(E3DC.CONNECT_WEB, username=USERNAME, password=PASS, serialNumber = SERIALNUMBER, isPasswordMd5=True, configuration = CONFIG)
# connect to the portal and poll the status. This might raise an exception in case of failed login. This operation is performed with Ajax

e3dc_status = e3dc.poll();
print(e3dc_status)
print(e3dc_status['consumption']['house'])

 


# Poll the status of the switches using a remote RSCP connection via websockets
# return value is in the format {'id': switchID, 'type': switchType, 'name': switchName, 'status': switchStatus}
#print(e3dc.poll_switches())
