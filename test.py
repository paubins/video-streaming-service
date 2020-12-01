#! /usr/bin/env python3.6

"""
server.py
Stripe Recipe.
Python 3.6 or newer required.
"""

import stripe
import json
import os
import uuid

from flask import Flask, render_template, jsonify, request, send_from_directory
from dotenv import load_dotenv, find_dotenv

from celery import Celery

import CloudFlare
from linode_api4 import LinodeClient, Image

import time
import dataset

db = dataset.connect(os.getenv('DATABASE_URL'))
table = db['device']
print(table)
# user = table.find_one(user_id=user_id)