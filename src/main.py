import argparse

import mm_calls
from log import logging

if __name__ == '__main__':
    logging.info("Starting MM...")

    mm_instance = mm_calls.MMInteractions()
    mm_instance.mm_login()
    
    # In case there are existing wagers, cancel them (shouldn't happen)
    mm_instance.cancel_all_wagers()

    mm_instance.get_balance()
    mm_instance.seeding()
    mm_instance.subscribe()  # subscribe to various public and private channels
    mm_instance.auto_betting()
    # mm_instance.keep_alive()

    # Cancel all outstanding orders
    mm_instance.cancel_all_wagers()

