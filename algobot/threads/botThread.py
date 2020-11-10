import traceback
import helpers

from PyQt5.QtCore import QObject, pyqtSignal, QRunnable, pyqtSlot

from data import Data
from datetime import datetime, timedelta
from enums import LIVE, SIMULATION, BEARISH, BULLISH
from realtrader import RealTrader
from simulationtrader import SimulationTrader
from telegramBot import TelegramBot


class BotSignals(QObject):
    started = pyqtSignal(int)
    activity = pyqtSignal(int, str)
    updated = pyqtSignal(int, dict)
    finished = pyqtSignal()
    error = pyqtSignal(int, str)


class BotThread(QRunnable):
    def __init__(self, caller: int, gui):
        super(BotThread, self).__init__()
        self.signals = BotSignals()
        self.gui = gui
        self.nextScheduledEvent = None
        self.scheduleSeconds = None
        self.telegramChatID = gui.configuration.telegramChatID.text()
        self.caller = caller
        self.trader = None

    def initialize_lower_interval_trading(self, caller, interval: str):
        """
        Initializes lower interval trading data object.
        :param caller: Caller that determines whether lower interval is for simulation or live bot.
        :param interval: Current interval for simulation or live bot.
        """
        sortedIntervals = ('1m', '3m', '5m', '15m', '30m', '1h', '2h', '12h', '4h', '6h', '8h', '1d', '3d')
        gui = self.gui
        if interval != '1m':
            lowerInterval = sortedIntervals[sortedIntervals.index(interval) - 1]
            self.signals.activity.emit(caller, f'Retrieving data for lower interval {lowerInterval}...')
            if caller == LIVE:
                gui.lowerIntervalData = Data(lowerInterval)
            elif caller == SIMULATION:
                gui.simulationLowerIntervalData = Data(lowerInterval)
            else:
                raise TypeError("Invalid type of caller specified.")
            self.signals.activity.emit(caller, "Retrieved lower interval data successfully.")

    def create_trader(self, caller):
        """
        Creates a trader based on caller specified.
        :param caller: Caller that determines what type of trader will be created.
        """
        gui = self.gui
        configDict = gui.interfaceDictionary[caller]['configuration']
        symbol = configDict['ticker'].currentText()
        interval = helpers.convert_interval(configDict['interval'].currentText())

        if caller == SIMULATION:
            startingBalance = gui.configuration.simulationStartingBalanceSpinBox.value()
            self.signals.activity.emit(caller, f"Retrieving data for interval {interval}...")
            gui.simulationTrader = SimulationTrader(startingBalance=startingBalance,
                                                    symbol=symbol,
                                                    interval=interval,
                                                    loadData=True)
        elif caller == LIVE:
            apiSecret = gui.configuration.binanceApiSecret.text()
            apiKey = gui.configuration.binanceApiKey.text()
            tld = 'com' if gui.configuration.otherRegionRadio.isChecked() else 'us'
            isIsolated = gui.configuration.isolatedMarginAccountRadio.isChecked()
            self.check_api_credentials(apiKey=apiKey, apiSecret=apiSecret)
            self.signals.activity.emit(caller, f"Retrieving data for interval {interval}...")
            gui.trader = RealTrader(apiSecret=apiSecret, apiKey=apiKey, interval=interval, symbol=symbol, tld=tld,
                                    isIsolated=isIsolated)
        else:
            raise ValueError("Invalid caller.")

        self.signals.activity.emit(caller, "Retrieved data successfully.")

        if configDict['lowerIntervalCheck'].isChecked():
            self.initialize_lower_interval_trading(caller=caller, interval=interval)

    @staticmethod
    def check_api_credentials(apiKey, apiSecret):
        """
        Helper function that checks API credentials specified. Needs to have more tests.
        :param apiKey: API key for Binance. (for now)
        :param apiSecret: API secret for Binance. (for now)
        """
        if len(apiSecret) == 0:
            raise ValueError('Please specify an API secret key. No API secret key found.')
        elif len(apiKey) == 0:
            raise ValueError("Please specify an API key. No API key found.")

    def initialize_scheduler(self):
        gui = self.gui
        measurement = gui.configuration.schedulingTimeUnit.value()
        unit = gui.configuration.schedulingIntervalComboBox.currentText()

        if unit == "Seconds":
            seconds = measurement
        elif unit == "Minutes":
            seconds = measurement * 60
        elif unit == "Hours":
            seconds = measurement * 3600
        elif unit == "Days":
            seconds = measurement * 3600 * 24
        else:
            raise ValueError("Invalid type of unit.")

        self.scheduleSeconds = seconds
        self.nextScheduledEvent = datetime.now() + timedelta(seconds=seconds)

    def handle_scheduler(self):
        if self.nextScheduledEvent is not None and datetime.now() >= self.nextScheduledEvent:
            self.gui.telegramBot.send_statistics_telegram(self.telegramChatID, self.scheduleSeconds)
            self.nextScheduledEvent = datetime.now() + timedelta(seconds=self.scheduleSeconds)

    def setup_bot(self, caller):
        """
        Initial full bot setup based on caller.
        :param caller: Caller that will determine what type of trader will be instantiated.
        """
        self.create_trader(caller)
        self.gui.set_parameters(caller)
        self.trader = self.gui.get_trader(caller)

        if caller == LIVE:
            if self.gui.configuration.enableTelegramTrading.isChecked():
                self.handle_telegram_bot()
            if self.gui.configuration.schedulingStatisticsCheckBox.isChecked():
                self.initialize_scheduler()
            self.gui.runningLive = True
        elif caller == SIMULATION:
            self.gui.simulationRunningLive = True
        else:
            raise RuntimeError("Invalid type of caller specified.")

    def update_data(self, caller):
        """
        Updates data if updated data exists for caller object.
        :param caller: Object type that will be updated.
        """
        trader = self.gui.get_trader(caller)
        if not trader.dataView.data_is_updated():
            # self.signals.activity.emit(caller, 'New data found. Updating...')
            trader.dataView.update_data()
            # self.signals.activity.emit(caller, 'Updated data successfully.')

    def handle_trading(self, caller):
        """
        Handles trading by checking if automation mode is on or manual.
        :param caller: Object for which function will handle trading.
        """
        trader = self.gui.get_trader(caller)
        trader.main_logic()

    def handle_trailing_prices(self, caller):
        """
        Handles trailing prices for caller object.
        :param caller: Trailing prices for what caller to be handled for.
        """
        trader = self.gui.get_trader(caller)
        trader.currentPrice = trader.dataView.get_current_price()
        if trader.longTrailingPrice is not None and trader.currentPrice > trader.longTrailingPrice:
            trader.longTrailingPrice = trader.currentPrice
        if trader.shortTrailingPrice is not None and trader.currentPrice < trader.shortTrailingPrice:
            trader.shortTrailingPrice = trader.currentPrice

    def handle_logging(self, caller):
        """
        Handles logging type for caller object.
        :param caller: Object those logging will be performed.
        """
        if self.gui.advancedLogging:
            self.gui.get_trader(caller).output_basic_information()

    def handle_telegram_bot(self):
        """
        Attempts to initiate Telegram bot.
        """
        gui = self.gui
        if gui.telegramBot is None:
            apiKey = gui.configuration.telegramApiKey.text()
            gui.telegramBot = TelegramBot(gui=gui, token=apiKey)
        gui.telegramBot.start()
        # try:
        #     gui = self.gui
        #     if gui.telegramBot is None:
        #         apiKey = gui.configuration.telegramApiKey.text()
        #         gui.telegramBot = TelegramBot(gui=gui, token=apiKey)
        #     gui.telegramBot.start()
        #     self.signals.activity.emit(LIVE, 'Started Telegram bot.')
        # except InvalidToken:
        #     self.signals.activity.emit(LIVE, 'Invalid token for Telegram. Please recheck credentials in settings.')

    def handle_lower_interval_cross(self, caller, previousLowerTrend) -> bool:
        """
        Handles logic and notifications for lower interval cross data.
        :param previousLowerTrend: Previous lower trend. Used to check if notification is necessary.
        :param caller: Caller for which we will check lower interval cross data.
        """
        trader = self.gui.get_trader(caller)
        lowerData = self.gui.get_lower_interval_data(caller)
        lowerTrend = trader.get_trend(dataObject=lowerData)
        trend = trader.trend
        if previousLowerTrend == lowerTrend or lowerTrend == trend:
            return lowerTrend
        else:
            trends = {BEARISH: 'Bearish', BULLISH: 'Bullish', None: 'No'}
            message = f'{trends[lowerTrend]} trend detected on lower interval data.'
            self.signals.activity.emit(caller, message)
            if self.gui.configuration.enableTelegramNotification.isChecked():
                self.gui.telegramBot.send_message(message=message, chatID=self.telegramChatID)
            return lowerTrend

    # to fix
    def handle_cross_notification(self, caller, notification):
        """
        Handles cross notifications.
        :param caller: Caller object for whom function will handle cross notifications.
        :param notification: Notification boolean whether it is time to notify or not.
        :return: Boolean whether cross should be notified on next function call.
        """
        gui = self.gui
        if caller == SIMULATION:
            if gui.simulationTrader.currentPosition is None:
                if not gui.simulationTrader.inHumanControl and notification:
                    gui.add_to_simulation_activity_monitor("Waiting for a cross.")
                    return False
            else:
                return False
        elif caller == LIVE:
            if gui.trader.currentPosition is not None:
                return False
            else:
                if not notification and not gui.trader.inHumanControl:
                    gui.add_to_live_activity_monitor("Waiting for a cross.")
                    return False
        else:
            raise ValueError("Invalid type of caller or cross notification specified.")

    def get_statistics(self):
        trader = self.trader
        net = trader.get_net()
        profit = trader.get_profit()
        stopLoss = trader.get_stop_loss()
        profitLabel = trader.get_profit_or_loss_string(profit=profit)
        percentage = trader.get_profit_percentage(trader.startingBalance, net)
        currentPriceString = f'${trader.dataView.get_current_price()}'
        percentageString = f'{round(percentage, 2)}%'
        profitString = f'${abs(round(profit, 2))}'
        netString = f'${round(net, 2)}'

        optionDetails = []
        for option in trader.tradingOptions:
            optionDetails.append(self.gui.get_option_info(option, trader))

        updateDict = {
            # Statistics window
            'net': net,
            'startingBalanceValue': f'${round(trader.startingBalance, 2)}',
            'currentBalanceValue': f'${round(trader.balance, 2)}',
            'netValue': netString,
            'profitLossLabel': profitLabel,
            'profitLossValue': profitString,
            'percentageValue': percentageString,
            'tradesMadeValue': str(len(trader.trades)),
            'coinOwnedLabel': f'{trader.coinName} Owned',
            'coinOwnedValue': f'{round(trader.coin, 6)}',
            'coinOwedLabel': f'{trader.coinName} Owed',
            'coinOwedValue': f'{round(trader.coinOwed, 6)}',
            'lossPointLabel': trader.get_stop_loss_strategy_string(),
            'lossPointValue': trader.get_safe_rounded_string(stopLoss),
            'customStopPointValue': trader.get_safe_rounded_string(trader.customStopLoss),
            'currentPositionValue': trader.get_position_string(),
            'autonomousValue': str(not trader.inHumanControl),
            'tickerLabel': trader.symbol,
            'tickerValue': currentPriceString,
            'optionDetails': optionDetails
        }

        return updateDict

    def trading_loop(self, caller):
        """
        Main loop that runs based on caller.
        :param caller: Caller object that determines which bot is running.
        """
        lowerTrend = None
        runningLoop = self.gui.runningLive if caller == LIVE else self.gui.simulationRunningLive
        if self.nextScheduledEvent is not None:
            self.gui.telegramBot.send_message(self.telegramChatID, "Initiated periodic statistics notification.")

        while runningLoop:
            self.update_data(caller)
            self.handle_logging(caller=caller)
            self.handle_trailing_prices(caller=caller)
            self.handle_trading(caller=caller)
            self.handle_scheduler()
            # crossNotification = self.handle_cross_notification(caller=caller, notification=crossNotification)
            lowerTrend = self.handle_lower_interval_cross(caller, lowerTrend)
            statDict = self.get_statistics()
            self.signals.updated.emit(caller, statDict)
            runningLoop = self.gui.runningLive if caller == LIVE else self.gui.simulationRunningLive

    @pyqtSlot()
    def run(self):
        """
        Initialise the runner function with passed args, kwargs.
        """
        # Retrieve args/kwargs here; and fire processing using them
        try:
            caller = self.caller
            self.setup_bot(caller=caller)
            self.signals.started.emit(caller)
            self.trading_loop(caller)
        except Exception as e:
            print(f'Error: {e}')
            traceback.print_exc()
            self.signals.error.emit(self.caller, str(e))
