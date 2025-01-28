from api_helper import ShoonyaApiPy, get_time
import datetime
import logging
import time
import yaml
import pandas as pd
from colorama import init, Fore, Style

init(autoreset=True)
logging.basicConfig(level=logging.DEBUG)

# flag to tell us if the websocket is open
socket_opened = False

# application callbacks
def event_handler_order_update(message):
    print(Fore.YELLOW + "Order event: " + str(message))

def event_handler_quote_update(message):
    print(Fore.YELLOW + "Quote event: " + str(message))

def open_callback():
    global socket_opened
    socket_opened = True
    print(Fore.CYAN + 'App is connected')
    # api.subscribe_orders()
    api.subscribe('NSE|22')
    # api.subscribe(['NSE|22', 'BSE|522032'])

# end of callbacks

# start of our program
api = ShoonyaApiPy()

# yaml for parameters
with open('cred.yml') as f:
    cred = yaml.load(f, Loader=yaml.FullLoader)
    print(Fore.GREEN + str(cred))

ret = api.login(userid=cred['user'], password=cred['pwd'], twoFA=cred['factor2'], vendor_code=cred['vc'], api_secret=cred['apikey'], imei=cred['imei'])

if ret is not None:
    while True:
        print(Fore.CYAN + 'p => place order')
        print(Fore.CYAN + 'm => modify order')
        print(Fore.CYAN + 'c => cancel order')
        print(Fore.CYAN + 'y => order history')
        print(Fore.CYAN + 'o => get order book')
        print(Fore.CYAN + 'h => get holdings')
        print(Fore.CYAN + 'l => get limits')
        print(Fore.CYAN + 'k => get positions')
        print(Fore.CYAN + 'd => get daily mtm')
        print(Fore.CYAN + 's => start_websocket')
        print(Fore.CYAN + 'q => quit')

        prompt1 = input(Fore.CYAN + 'What shall we do? ').lower()

        if prompt1 == 'p':
            ret = api.place_order(buy_or_sell='B', product_type='C',
                                  exchange='NSE', tradingsymbol='INFY-EQ',
                                  quantity=1, discloseqty=0, price_type='LMT', price=1500.00, trigger_price=None,
                                  retention='DAY', remarks='my_order_001')
            print(Fore.GREEN + str(ret))

        elif prompt1 == 'm':
            orderno = input(Fore.CYAN + 'Enter orderno: ').lower()
            ret = api.modify_order(exchange='NSE', tradingsymbol='INFY-EQ', orderno=orderno,
                                   newquantity=2, newprice_type='LMT', newprice=1505.00)
            print(Fore.GREEN + str(ret))

        elif prompt1 == 'c':
            orderno = input(Fore.CYAN + 'Enter orderno: ').lower()
            ret = api.cancel_order(orderno=orderno)
            print(Fore.GREEN + str(ret))

        elif prompt1 == 'y':
            orderno = input(Fore.CYAN + 'Enter orderno: ').lower()
            ret = api.single_order_history(orderno=orderno)
            print(Fore.GREEN + str(ret))

        elif prompt1 == 'o':
            ret = api.get_order_book()
            print(Fore.GREEN + str(ret))

        elif prompt1 == 'h':
            ret = api.get_holdings()
            print(Fore.GREEN + str(ret))

        elif prompt1 == 'l':
            ret = api.get_limits()
            print(Fore.GREEN + str(ret))

        elif prompt1 == 'k':
            ret = api.get_positions()
            print(Fore.GREEN + str(ret))

        elif prompt1 == 'd':
            # contributed by Aromal P Nair
            while True:
                ret = api.get_positions()
                mtm = 0
                pnl = 0
                for i in ret:
                    mtm += float(i['urmtom'])
                    pnl += float(i['rpnl'])
                    day_m2m = mtm + pnl
                print(Fore.GREEN + str(day_m2m))

        elif prompt1 == 's':
            if socket_opened:
                print(Fore.RED + 'Websocket already opened')
                continue
            ret = api.start_websocket(order_update_callback=event_handler_order_update, subscribe_callback=event_handler_quote_update, socket_open_callback=open_callback)
            print(Fore.GREEN + str(ret))
        else:
            print(Fore.RED + 'Fin')
            break

