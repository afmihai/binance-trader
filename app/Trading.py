# -*- coding: UTF-8 -*-
# @yasinkuyu

# Define Python imports
import os
import sys
import time
import config
import threading
import math
import logging
import logging.handlers

# Define Custom imports
from Database import Database
from Orders import Orders

formatter_str = '%(asctime)s,%(msecs)d %(levelname)s %(name)s: %(message)s'
formatter = logging.Formatter(formatter_str)
datefmt = "%Y-%b-%d %H:%M:%S"

LOGGER_ENUM = {'debug': 'debug.log', 'trading': 'trades.log', 'errors': 'general.log'}
# LOGGER_FILE = LOGGER_ENUM['pre']
LOGGER_FILE = "binance-trader.log"
FORMAT = '%(asctime)-15s - %(levelname)s:  %(message)s'

logging.basicConfig(filename=LOGGER_FILE, filemode='a',
                    format=formatter_str, datefmt=datefmt,
                    level=logging.INFO)

# Approximated value to get back the commission for sell and buy
TOKEN_COMMISSION = 0.001
BNB_COMMISSION = 0.0005


# ((eth*0.05)/100)


class Trading:
    # Define trade vars
    order_id = 0
    order_data = None

    buy_filled = True
    sell_filled = True

    buy_filled_qty = 0
    sell_filled_qty = 0

    # percent (When you drop 10%, sell panic.)
    stop_loss = 0

    # Buy/Sell qty
    quantity = 0

    # BTC amount
    amount = 0

    # float(step_size * math.floor(float(free)/step_size))
    step_size = 0

    # Define static vars
    WAIT_TIME_BUY_SELL = 1  # seconds
    WAIT_TIME_CHECK_BUY_SELL = 0.2  # seconds
    WAIT_TIME_CHECK_SELL = 5  # seconds
    WAIT_TIME_STOP_LOSS = 20  # seconds

    MAX_TRADE_SIZE = 7  # int

    # Type of commission, Default BNB_COMMISSION
    commission = BNB_COMMISSION

    def __init__(self, option):
        print("options: {0}".format(option))

        # Get argument parse options
        self.option = option

        # Define parser vars
        self.order_id = self.option.orderid
        self.quantity = self.option.quantity
        self.wait_time = self.option.wait_time
        self.stop_loss = self.option.stop_loss

        self.increasing = self.option.increasing
        self.decreasing = self.option.decreasing

        # BTC amount
        self.amount = self.option.amount

        # Type of commission
        if self.option.commission == 'TOKEN':
            self.commission = TOKEN_COMMISSION

        # setup Logger
        self.logger = self.setup_logger(self.option.symbol, debug=self.option.debug)

    def setup_logger(self, symbol, debug=True):
        """Function setup as many loggers as you want"""
        # handler = logging.FileHandler(log_file)
        # handler.setFormatter(formatter)
        # logger.addHandler(handler)
        logger = logging.getLogger(symbol)

        stout_handler = logging.StreamHandler(sys.stdout)
        if debug:
            logger.setLevel(logging.DEBUG)
            stout_handler.setLevel(logging.DEBUG)

        # handler = logging.handlers.SysLogHandler(address='/dev/log')
        # logger.addHandler(handler)
        stout_handler.setFormatter(formatter)
        logger.addHandler(stout_handler)
        return logger

    def buy(self, symbol, quantity, buyPrice, profitableSellingPrice):

        # Do you have an open order?
        self.check_order()

        try:

            # Create order
            order_id = Orders.buy_limit(symbol, quantity, buyPrice)

            # Database log
            Database.write([order_id, symbol, 0, buyPrice, 'BUY', quantity, self.option.profit])

            # print('Buy order created id:%d, q:%.8f, p:%.8f' % (order_id, quantity, float(buyPrice)))
            self.logger.info('%s : Buy order created id:%d, q:%.8f, p:%.8f, Take profit approx :%.8f' % (
                symbol, order_id, quantity, float(buyPrice), profitableSellingPrice))

            self.order_id = order_id

            return order_id

        except Exception as e:
            # print('bl: %s' % (e))
            self.logger.debug('Buy error: %s' % e)
            time.sleep(self.WAIT_TIME_BUY_SELL)
            return None

    def sell(self, symbol, quantity, order_id, sell_price, last_price):

        '''
        The specified limit will try to sell until it reaches.
        If not successful, the order will be canceled.
        '''

        buy_order = Orders.get_order(symbol, order_id)

        if buy_order['status'] == 'FILLED' and buy_order['side'] == 'BUY':
            # print('Buy order filled... Try sell...')
            self.logger.info('Buy order filled... Try sell...')
        else:
            time.sleep(self.WAIT_TIME_CHECK_BUY_SELL)
            if buy_order['status'] == 'FILLED' and buy_order['side'] == 'BUY':
                # print('Buy order filled after 0.1 second... Try sell...')
                self.logger.info('Buy order filled after 0.1 second... Try sell...')
            elif buy_order['status'] == 'PARTIALLY_FILLED' and buy_order['side'] == 'BUY':
                # print('Buy order partially filled... Try sell... Cancel remaining buy...')
                self.logger.info('Buy order partially filled... Try sell... Cancel remaining buy...')
                self.cancel(symbol, order_id)
            else:
                self.cancel(symbol, order_id)
                # print('Buy order fail (Not filled) Cancel order...')
                self.logger.warning('Buy order fail (Not filled) Cancel order...')
                self.order_id = 0
                return

        sell_order = Orders.sell_limit(symbol, quantity, sell_price)

        sell_id = sell_order['order_id']
        # print('Sell order create id: %d' % sell_id)
        self.logger.info('Sell order create id: %d' % sell_id)

        time.sleep(self.WAIT_TIME_CHECK_SELL)

        if sell_order['status'] == 'FILLED':
            # print('Sell order (Filled) Id: %d' % sell_id)
            # print('LastPrice : %.8f' % last_price)
            # print('Profit: %%%s. Buy price: %.8f Sell price: %.8f' % (self.option.profit, float(sell_order['price']), sell_price))

            self.logger.info('Sell order (Filled) Id: %d' % sell_id)
            self.logger.info('LastPrice : %.8f' % last_price)
            self.logger.info('Profit: %%%s. Buy price: %.8f Sell price: %.8f' % (
                self.option.profit, float(sell_order['price']), sell_price))

            self.order_id = 0
            self.order_data = None

            return

        '''
        If all sales trials fail, 
        the grievance is stop-loss.
        '''

        if self.stop_loss > 0:

            # If sell order failed after 5 seconds, 5 seconds more wait time before selling at loss
            time.sleep(self.WAIT_TIME_CHECK_SELL)

            if self.stop(symbol, quantity, sell_id, last_price):

                if Orders.get_order(symbol, sell_id)['status'] != 'FILLED':
                    # print('We apologize... Sold at loss...')
                    self.logger.info('We apologize... Sold at loss...')

            else:
                # print('We apologize... Cant sell even at loss... Please sell manually... Stopping program...')
                self.logger.info(
                    'We apologize... Cant sell even at loss... Please sell manually... Stopping program...')
                self.cancel(symbol, sell_id)
                exit(1)

            while sell_status != 'FILLED':
                time.sleep(self.WAIT_TIME_CHECK_SELL)
                sell_status = Orders.get_order(symbol, sell_id)['status']
                lastPrice = Orders.get_ticker(symbol)
                # print('Status: %s Current price: %.8f Sell price: %.8f' % (sell_status, lastPrice, sell_price))
                # print('Sold! Continue trading...')

                self.logger.info(
                    'Status: %s Current price: %.8f Sell price: %.8f' % (sell_status, lastPrice, sell_price))
                self.logger.info('Sold! Continue trading...')

            self.order_id = 0
            self.order_data = None

    def stop(self, symbol, quantity, order_id, last_price):
        # If the target is not reached, stop-loss.
        stop_order = Orders.get_order(symbol, order_id)

        stop_price = self.calc(float(stop_order['price']))

        loss_price = stop_price - (stop_price * self.stop_loss / 100)

        status = stop_order['status']

        # Order status
        if status == 'NEW' or status == 'PARTIALLY_FILLED':

            if self.cancel(symbol, order_id):

                sell_order = Orders.sell_market(symbol, quantity)

                sell_id = sell_order['order_id']
                # Stop loss
                if last_price >= loss_price:

                    # print('Stop-loss, sell market, %s' % (last_price))
                    self.logger.info('Stop-loss, sell market, %s' % last_price)

                    if sell_order == True:
                        return True
                    else:
                        # Wait a while after the sale to the loss.
                        time.sleep(self.WAIT_TIME_STOP_LOSS)
                        status_loss = sell_order['status']
                        if status_loss != 'NEW':
                            print('Stop-loss, sold')
                            self.logger.info('Stop-loss, sold')
                            return True
                        else:
                            self.cancel(symbol, sell_id)
                            return False
                else:
                    sell_order = Orders.sell_limit(symbol, quantity, loss_price)
                    print('Stop-loss, sell limit, %s' % loss_price)
                    time.sleep(self.WAIT_TIME_STOP_LOSS)
                    status_loss = sell_order['status']
                    if status_loss != 'NEW':
                        print('Stop-loss, sold')
                        return True
                    else:
                        self.cancel(symbol, sell_id)
                        return False
            else:
                print('Cancel did not work... Might have been sold before stop loss...')
                return True

        elif status == 'FILLED':
            self.order_id = 0
            self.order_data = None
            print('Order filled')
            return True
        else:
            return False

    def check(self, symbol, order_id, quantity):
        # If profit is available and there is no purchase from the specified price, take it with the market.

        # Do you have an open order?
        self.check_order()

        trading_size = 0
        time.sleep(self.WAIT_TIME_BUY_SELL)

        while trading_size < self.MAX_TRADE_SIZE:

            # Order info
            order = Orders.get_order(symbol, order_id)

            side = order['side']
            price = float(order['price'])

            # TODO: Sell partial qty
            orig_qty = float(order['origQty'])
            self.buy_filled_qty = float(order['executedQty'])

            status = order['status']

            # print('Wait buy order: %s id:%d, price: %.8f, orig_qty: %.8f' % (symbol, order['order_id'], price, orig_qty))
            self.logger.info(
                'Wait buy order: %s id:%d, price: %.8f, orig_qty: %.8f' % (symbol, order['order_id'], price, orig_qty))

            if status == 'NEW':

                if self.cancel(symbol, order_id):

                    buy_order = Orders.buy_market(symbol, quantity)

                    # print('Buy market order')
                    self.logger.info('Buy market order')

                    self.order_id = buy_order['order_id']
                    self.order_data = buy_order

                    if buy_order == True:
                        break
                    else:
                        trading_size += 1
                        continue
                else:
                    break

            elif status == 'FILLED':
                self.order_id = order['order_id']
                self.order_data = order
                # print('Filled')
                self.logger.info('Filled')
                break
            elif status == 'PARTIALLY_FILLED':
                # print('Partial filled')
                self.logger.info('Partial filled')
                break
            else:
                trading_size += 1
                continue

    def cancel(self, symbol, order_id):
        # If order is not filled, cancel it.
        check_order = Orders.get_order(symbol, order_id)

        if not check_order:
            self.order_id = 0
            self.order_data = None
            return True

        if check_order['status'] == 'NEW' or check_order['status'] != 'CANCELLED':
            Orders.cancel_order(symbol, order_id)
            self.order_id = 0
            self.order_data = None
            return True

    def calc(self, lastBid):
        try:

            # Estimated sell price considering commission
            return lastBid + (lastBid * self.option.profit / 100) + (lastBid * self.commission)
            # return lastBid + (lastBid * self.option.profit / 100)

        except Exception as e:
            print('Calc Error: %s' % e)
            return

    def check_order(self):
        # If there is an open order, exit.
        if self.order_id > 0:
            exit(1)

    def action(self, symbol):
        # import ipdb; ipdb.set_trace()

        # Order amount
        quantity = self.quantity

        # Fetches the ticker price
        last_price = Orders.get_ticker(symbol)

        # Order book prices
        last_bid, last_ask = Orders.get_order_book(symbol)

        # Target buy price, add little increase #87
        buy_price = last_bid + self.increasing

        # Target sell price, decrease little
        sell_price = last_ask - self.decreasing

        # Spread ( profit )
        profitable_selling_price = self.calc(last_bid)

        # Check working mode
        if self.option.mode == 'range':
            buy_price = float(self.option.buyprice)
            sell_price = float(self.option.sellprice)
            profitable_selling_price = sell_price

        # Screen log
        if self.option.prints and self.order_id == 0:
            spread_percent = (last_ask / last_bid - 1) * 100.0
            # print('price:%.8f buyp:%.8f sellp:%.8f-bid:%.8f ask:%.8f spread:%.2f' % (last_price, buy_price, profitable_selling_price, last_bid, last_ask, spread_percent))
            self.logger.debug(
                'price:%.8f buyprice:%.8f sellprice:%.8f bid:%.8f ask:%.8f spread:%.2f  Originalsellprice:%.8f' % (
                    last_price, buy_price, profitable_selling_price, last_bid, last_ask, spread_percent,
                    profitable_selling_price - (last_bid * self.commission)))

        # analyze = threading.Thread(target=analyze, args=(symbol,))
        # analyze.start()

        if self.order_id > 0:

            # Profit mode
            if self.order_data is not None:

                order = self.order_data

                # Last control
                new_profitable_selling_price = self.calc(float(order['price']))

                if last_ask >= new_profitable_selling_price:
                    profitable_selling_price = new_profitable_selling_price

            # range mode
            if self.option.mode == 'range':
                profitable_selling_price = self.option.sellprice

            '''            
            If the order is complete, 
            try to sell it.
            '''

            # Perform buy action
            sell_action = threading.Thread(target=self.sell,
                                           args=(
                                               symbol, quantity, self.order_id, profitable_selling_price, last_price,))
            sell_action.start()

            return

        '''
        Did profit get caught
        if ask price is greater than profit price, 
        buy with my buy price,    
        '''
        if (last_ask >= profitable_selling_price and self.option.mode == 'profit') or \
                (last_price <= float(self.option.buyprice) and self.option.mode == 'range'):
            self.logger.info("MOde: {0}, Lastsk: {1}, Profit Sell Price {2}, ".format(self.option.mode, last_ask,
                                                                                      profitable_selling_price))

            if self.order_id == 0:
                self.buy(symbol, quantity, buy_price, profitable_selling_price)

                # Perform check/sell action
                # checkAction = threading.Thread(target=self.check, args=(symbol, self.order_id, quantity,))
                # checkAction.start()

    def logic(self):
        return 0

    def filters(self):

        symbol = self.option.symbol

        # Get symbol exchange info
        symbol_info = Orders.get_info(symbol)

        if not symbol_info:
            # print('Invalid symbol, please try again...')
            self.logger.error('Invalid symbol, please try again...')
            exit(1)

        symbol_info['filters'] = {item['filterType']: item for item in symbol_info['filters']}

        return symbol_info

    def format_step(self, quantity, stepSize):
        return float(stepSize * math.floor(float(quantity) / stepSize))

    def validate(self):

        valid = True
        symbol = self.option.symbol
        filters = self.filters()['filters']

        # Order book prices
        last_bid, last_ask = Orders.get_order_book(symbol)

        last_price = Orders.get_ticker(symbol)

        min_qty = float(filters['LOT_SIZE']['min_qty'])
        min_price = float(filters['PRICE_FILTER']['minPrice'])
        min_notional = float(filters['MIN_NOTIONAL']['minNotional'])
        quantity = float(self.option.quantity)

        # stepSize defines the intervals that a quantity/icebergQty can be increased/decreased by.
        step_size = float(filters['LOT_SIZE']['stepSize'])

        # tickSize defines the intervals that a price/stopPrice can be increased/decreased by
        tick_size = float(filters['PRICE_FILTER']['tickSize'])

        # If option increasing default tickSize greater than
        if float(self.option.increasing) < tick_size:
            self.increasing = tick_size

        # If option decreasing default tickSize greater than
        if float(self.option.decreasing) < tick_size:
            self.decreasing = tick_size

        # Just for validation
        last_bid = last_bid + self.increasing

        # Set static
        # If quantity or amount is zero, minNotional increase 10%
        quantity = (min_notional / last_bid)
        quantity = quantity + (quantity * 10 / 100)
        notional = min_notional

        if self.amount > 0:
            # Calculate amount to quantity
            quantity = (self.amount / last_bid)

        if self.quantity > 0:
            # Format quantity step
            quantity = self.quantity

        quantity = self.format_step(quantity, step_size)
        notional = last_bid * float(quantity)

        # Set Globals
        self.quantity = quantity
        self.step_size = step_size

        # min_qty = minimum order quantity
        if quantity < min_qty:
            # print('Invalid quantity, min_qty: %.8f (u: %.8f)' % (min_qty, quantity))
            self.logger.error('Invalid quantity, min_qty: %.8f (u: %.8f)' % (min_qty, quantity))
            valid = False

        if last_price < min_price:
            # print('Invalid price, minPrice: %.8f (u: %.8f)' % (minPrice, last_price))
            self.logger.error('Invalid price, minPrice: %.8f (u: %.8f)' % (min_price, last_price))
            valid = False

        # minNotional = minimum order value (price * quantity)
        if notional < min_notional:
            # print('Invalid notional, minNotional: %.8f (u: %.8f)' % (minNotional, notional))
            self.logger.error('Invalid notional, minNotional: %.8f (u: %.8f)' % (min_notional, notional))
            valid = False

        if not valid:
            exit(1)

    def run(self):

        cycle = 0
        actions = []

        symbol = self.option.symbol

        print('Auto Trading for Binance.com @yasinkuyu')
        print('\n')

        # Validate symbol
        self.validate()

        print('Started...')
        print('Trading Symbol: %s' % symbol)
        print('Buy Quantity: %.8f' % self.quantity)
        print('Stop-Loss Amount: %s' % self.stop_loss)
        # print('Estimated profit: %.8f' % (self.quantity*self.option.profit))

        if self.option.mode == 'range':

            if self.option.buyprice == 0 or self.option.sellprice == 0:
                print('Please enter --buyprice / --sellprice\n')
                exit(1)

            print('Range Mode Options:')
            print('\tBuy Price: %.8f', self.option.buyprice)
            print('\tSell Price: %.8f', self.option.sellprice)

        else:
            print('Profit Mode Options:')
            print('\tPreferred Profit: %0.2f%%' % self.option.profit)
            print('\tBuy Price : (Bid+ --increasing %.8f)' % self.increasing)
            print('\tSell Price: (Ask- --decreasing %.8f)' % self.decreasing)

        print('\n')

        start_time = time.time()

        """
        # DEBUG LINES
        action_trader = threading.Thread(target=self.action, args=(symbol,))
        actions.append(action_trader)
        action_trader.start()

        end_time = time.time()

        if end_time - start_time < self.wait_time:

            time.sleep(self.wait_time - (end_time - start_time))

            # 0 = Unlimited loop
            if self.option.loop > 0:
                cycle = cycle + 1

        """

        while cycle <= self.option.loop:

            start_time = time.time()

            action_trader = threading.Thread(target=self.action, args=(symbol,))
            actions.append(action_trader)
            action_trader.start()

            end_time = time.time()

            if end_time - start_time < self.wait_time:

                time.sleep(self.wait_time - (end_time - start_time))

                # 0 = Unlimited loop
                if self.option.loop > 0:
                    cycle = cycle + 1
