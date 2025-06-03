from api_helper import ShoonyaApiPy
import logging
 
#enable dbug to see request and responses
logging.basicConfig(level=logging.DEBUG)

#start of our program
api = ShoonyaApiPy()

#credentials
user    = 'FA50394'
pwd     = 'Arsh@987567'
vc      = 'FA50394_U'
app_key = '8ce994566d0ce1ca66b205ccbcdcfdf4'
imei    = 'xyz12345'

# Get 2FA code from user
factor2 = input("Please enter your 2FA code: ")

#make the api call
ret = api.login(userid=user, password=pwd, twoFA=factor2, vendor_code=vc, api_secret=app_key, imei=imei)

print(ret)

