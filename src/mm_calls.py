import time

import requests
import json
import pysher
import base64
import schedule
import random
import threading
import uuid

from urllib.parse import urljoin
from src import config
from src.log import logging
from src import consts


class MMInteractions:
    base_url: str = None
    balance: float = 0
    mm_keys: dict = dict()
    mm_session: dict = dict()
    all_tournaments: dict = dict()    # mapping from string to id
    my_tournaments: dict = dict()
    sport_events: dict = dict()   # key is event id, value is a list of event details and markets
    wagers: dict = dict()    # all wagers bet in the session

    def __init__(self):
        self.base_url = config.BASE_URL
        self.mm_keys = config.MM_KEYS

    def mm_login(self) -> dict:
        login_url = urljoin(self.base_url, config.URL['mm_login'])
        request_body = {
            'access_key': self.mm_keys.get('access_key'),
            'secret_key': self.mm_keys.get('secret_key'),
        }
        response = requests.post(login_url, data=json.dumps(request_body))
        if response.status_code != 200:
            logging.debug(response)
            raise Exception("login failed")
        mm_session = json.loads(response.content)['data']
        logging.info(mm_session)
        self.mm_session = mm_session
        logging.info("MM session started")
        return mm_session

    def seeding(self):
        # initiate available tournaments/sport_events
        # tournaments
        logging.info("start seeding tournaments/events/markets")
        t_url = urljoin(self.base_url, config.URL['mm_tournaments'])
        headers = self.__get_auth_header()
        all_tournaments_response = requests.get(t_url, headers=headers)
        if all_tournaments_response.status_code != 200:
            raise Exception("not able to seed tournaments")
        all_tournaments = json.loads(all_tournaments_response.content).get('data', {}).get('tournaments', {})
        self.all_tournaments = all_tournaments

        # get sportevents and markets of each
        event_url = urljoin(self.base_url, config.URL['mm_events'])
        market_url = urljoin(self.base_url, config.URL['mm_markets'])
        for one_t in all_tournaments:
            if one_t['name'] in config.TOURNAMENTS_INTERESTED:
                self.my_tournaments[one_t['id']] = one_t
                events_response = requests.get(event_url, params={'tournament_id': one_t['id']}, headers=headers)
                if events_response.status_code == 200:
                    events = json.loads(events_response.content).get('data', {}).get('sport_events')
                    for event in events:
                        market_response = requests.get(market_url, params={'event_id': event['event_id']},
                                                       headers=headers)
                        if market_response.status_code == 200:
                            markets = json.loads(market_response.content).get('data', {}).get('markets', {})
                            event['markets'] = markets
                            self.sport_events[event['event_id']] = event
                        else:
                            logging.info(f'failed to get markets of events {event["name"]}')
                else:
                    logging.info(f'skip tournament {one_t["name"]} as api request failed')

        logging.info("Done, seeding")
        logging.info(f"found {len(self.my_tournaments)} tournament, ingested {len(self.sport_events)} "
                     f"sport events from {len(config.TOURNAMENTS_INTERESTED)} tournaments")

    def subscribe(self):
        auth_endpoint_url = urljoin(self.base_url, config.URL['mm_auth'])
        auth_header = self.__get_auth_header()
        auth_headers = {
                           "Authorization": auth_header['Authorization'],
                           "header-subscriptions": '''[{"type":"tournament","ids":[]}]''',
                           "PartnerId": config.PARTNER_ID,  # TODO: what is this PartnerId
                       }
        pusher = pysher.Pusher(key=config.MM_APP_KEY, cluster=config.APP_CLUSTER,
                               auth_endpoint=auth_endpoint_url,
                               auth_endpoint_headers=auth_headers)

        def public_event_handler(*args, **kwargs):
            print("processing public, Args:", args)
            print(f"event details {base64.b64decode(json.loads(args[0]).get('payload', '{}'))}")
            print("processing public, Kwargs:", kwargs)

        def private_event_handler(*args, **kwargs):
            print("processing private, Args:", args)
            print("processing private, Kwargs:", kwargs)

        # We can't subscribe until we've connected, so we use a callback handler
        # to subscribe when able
        def connect_handler(data):
            public_channel = pusher.subscribe('private-broadcast-service=3-device_type=5')
            private_channel = pusher.subscribe(f'private-service=3-device_type=5-user={config.PARTNER_ID.replace("-", "")}')
            for t_id in self.my_tournaments:
                event_name = f'tournaments_{t_id}'
                public_channel.bind(event_name, public_event_handler)
                logging.info(f"subscribed to public channel, event name: {event_name}, successfully")
            # public_channel.bind('sport_events', public_event_handler)
            # public_channel.bind('markets', public_event_handler)


            # TODO: which event should I bind to get wagers/wallet status updates?
            private_channel.bind('wagers', private_event_handler)
            #logging.info("subscribed to private channel successfully")

        pusher.connection.bind('pusher:connection_established', connect_handler)
        pusher.connect()

    def get_balance(self):
        balance_url = urljoin(self.base_url, config.URL['mm_balance'])
        response = requests.get(balance_url, headers=self.__get_auth_header())
        if response.status_code != 200:
            logging.error("failed to get balance")
            return
        self.balance = json.loads(response.content).get('data', {}).get('balance', 0)
        logging.info(f"still have ${self.balance} left")

    def start_betting(self):
        logging.info("Start betting, randomly :)")
        for key in self.sport_events:
            one_event = self.sport_events[key]
            bet_url = urljoin(self.base_url, config.URL['mm_place_wager'])
            for market in one_event.get('markets', []):
                if market['type'] == 'moneyline':
                    # only bet on moneyline
                    if random.random() < 0.3:   # 30% chance to bet
                        for selection in market.get('selections', []):
                            if random.random() < 0.3: #30% chance to bet
                                odds_to_bet = self.__get_random_odds()
                                external_id = str(uuid.uuid1())
                                logging.info(f"going to bet on '{one_event['name']}' on moneyline, side {selection[0]['name']} with odds {odds_to_bet}")
                                body_to_send = {
                                    'external_id': external_id,
                                    'line_id': selection[0]['line_id'],
                                    'odds': odds_to_bet,
                                    'stake': 1.0
                                }
                                bet_response = requests.post(bet_url, json=body_to_send,
                                                             headers=self.__get_auth_header())
                                if bet_response.status_code != 200:
                                    logging.info(f"failed to bet, error {bet_response.content}")
                                else:
                                    logging.info("successfully")
                                    self.wagers[external_id] = json.loads(bet_response.content).get('data', {})['wager']['id']

    def random_cancel_wager(self):
        wager_keys = list(self.wagers.keys())
        for key in wager_keys:
            wager_id = self.wagers[key]
            cancel_url = urljoin(self.base_url, config.URL['mm_cancel_wager'])
            if random.random() < 0.5:  # 50% cancel
                logging.info("start to cancel wager")
                body = {
                    'external_id': key,
                    'wager_id': wager_id,
                }
                response = requests.post(cancel_url, json=body, headers=self.__get_auth_header())
                if response.status_code != 200:
                    if response.status_code == 404:
                        logging.info("already cancelled")
                        self.wagers.pop(key)
                    else:
                        logging.info("failed to cancel")
                else:
                    logging.info("cancelled successfully")
                    self.wagers.pop(key)

    def cancel_all_wagers(self):
        # TODO: upon urgency, I need to cancel all wagers, how to do it?
        print("cancel all wagers")

    def schedule_in_thread(self):
        while True:
            schedule.run_pending()
            time.sleep(1)

    def __auto_extend_session(self):
        # need to use new api, for now just create new session to pretend session extended
        self.mm_login()

    def auto_betting(self):
        logging.info("schedule to bet every 10 seconds")
        schedule.every(5).seconds.do(self.start_betting)
        schedule.every(7).seconds.do(self.random_cancel_wager)
        schedule.every(8).minutes.do(self.__auto_extend_session)

        child_thread = threading.Thread(target=self.schedule_in_thread, daemon=False)
        child_thread.start()

    def __get_auth_header(self) -> dict:
        return {
            'Authorization': f'Bearer '
                             f'{self.mm_session["access_token"]}',
            "PartnerId": config.PARTNER_ID,
        }

    def __get_random_odds(self):
        odds = consts.VALID_ODDS[random.randint(0, len(consts.VALID_ODDS) - 1)]
        return odds if random.random() < 0.5 else -1 * odds



