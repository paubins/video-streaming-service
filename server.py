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
import random, string
import requests
import tldextract

from flask import jsonify

from urllib.parse import parse_qs

import sendgrid
from sendgrid.helpers.mail import *

def make_celery(app):
    celery = Celery(
        app.import_name,
        backend=app.config['CELERY_RESULT_BACKEND'],
        broker=app.config['CELERY_BROKER_URL']
    )
    celery.conf.update(app.config)

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery


# Setup Stripe python client library
load_dotenv(find_dotenv())
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
stripe.api_version = os.getenv('STRIPE_API_VERSION')

static_dir = str(os.path.abspath(os.path.join(
    __file__, "..", os.getenv("STATIC_DIR"))))
app = Flask(__name__, static_folder=static_dir,
            static_url_path="", template_folder=static_dir)

app.config.update(
    CELERY_BROKER_URL=os.environ.get('REDIS_URL'),
    CELERY_RESULT_BACKEND=os.environ.get('REDIS_URL'),
)

celery = make_celery(app)

@celery.task()
def add_together(a, b):
    return a + b

@app.route('/', methods=['GET'])
def get_example():
    return render_template('index.html')

@app.route('/docs', methods=['GET'])
def get_about():
    return render_template('about.html')

@app.route('/publishable-key', methods=['GET'])
def get_publishable_key():
    return jsonify({'publishableKey': os.getenv('STRIPE_PUBLISHABLE_KEY')})

# Fetch the Checkout Session to display the JSON result on the success page
@app.route('/checkout-session', methods=['GET'])
def get_checkout_session():
    db = dataset.connect(os.getenv('DATABASE_URL'))
    table = db['device']
    user = table.find_one(stripe_session_id=request.args.get('sessionId'))
    return jsonify({
        "api_key" : user["identifer"]
    })

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    data = json.loads(request.data)
    domain_url = os.getenv('DOMAIN')
    price_id = os.getenv('SUBSCRIPTION_PRICE_ID')
    product_id = os.getenv('DONATION_PRODUCT_ID')
    line_items = [{"price": price_id, "quantity": 1}]

    try:
        if data['donation'] > 0:
            line_items.append(
                {"quantity": 1, "price_data": {"product": product_id, "unit_amount": data['donation'], "currency": "usd"}})
        # Sign customer up for subscription
        checkout_session = stripe.checkout.Session.create(
            mode="subscription",
            success_url=domain_url +
            "/success.html?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=domain_url + "/",
            payment_method_types=["card"],
            allow_promotion_codes=True,
            line_items=line_items
        )

        return jsonify({'checkoutSessionId': checkout_session['id']})
    except Exception as e:
        return jsonify(error=str(e)), 403


@app.route('/webhook', methods=['POST'])
def webhook_received():
    # You can use webhooks to receive information about asynchronous payment events.
    # For more about our webhook events check out https://stripe.com/docs/webhooks.
    webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    request_data = json.loads(request.data)

    if webhook_secret:
        # Retrieve the event by verifying the signature using the raw body and secret if webhook signing is configured.
        signature = request.headers.get('stripe-signature')
        try:
            event = stripe.Webhook.construct_event(
                payload=request.data, sig_header=signature, secret=webhook_secret)
            data = event['data']
        except Exception as e:
            return e
        # Get the type of webhook event sent - used to check the status of PaymentIntents.
        event_type = event['type']
    else:
        data = request_data['data']
        event_type = request_data['type']
    data_object = data['object']

    if event_type == 'checkout.session.completed':
        customer = stripe.Customer.retrieve(data_object["customer"])
        print(data_object)
        db = dataset.connect(os.getenv('DATABASE_URL'))
        table = db['device']
        table.insert(dict(
            email=customer.email,
            stripe_session_id=data_object["id"],
            subscription=data_object["subscription"],
            linode_id="", #new_linode.id
            ip_address="",
            password="",
            subdomain="",
            zone_id="",
            publish_webhook="",
            publish_end_webhook="",
            identifer=str(uuid.uuid4()),
            stream_token=""))

        setup_streaming_instance.delay(data_object["id"])

    return jsonify({'status': 'success'})

@app.route('/getStreamKey/', methods=['POST'])
def get_stream_key():
    db = dataset.connect(os.getenv('DATABASE_URL'))
    table = db['device']
    request_data = json.loads(request.data)
    user_id = request_data['api_key']
    user = table.find_one(identifer=user_id)
    print(user_id)
    print(user)
    subdomain = None
    if user["stream_token"] == "":
        stream_token = ''.join(random.choices(string.ascii_uppercase +
                             string.digits, k = 25)) 
        publish_webhook = request_data.get("publish_webhook")
        publish_end_webhook =  request_data.get("publish_end_webhook")
        table.update(dict(identifer=user_id,
            stream_token=stream_token,
            publish_webhook=publish_webhook,
            publish_end_webhook=publish_end_webhook), ["identifer"])
        subdomain = user["subdomain"].lower()
    else:
        stream_token = user["stream_token"]
        subdomain = user["subdomain"].lower()

    return jsonify({
        "stream_token" : stream_token,
        "rtmp_stream_endpoint" : f"rtmp://{subdomain}.enterprisesworldwide.com/live/{stream_token}",
        "hls_endoint" : f"http://{subdomain}.enterprisesworldwide.com/hls/{stream_token}/index.m3u8"
    })

# callbacks from nginx
@app.route('/publish/', methods=['POST'])
def publish():
    subdomain = tldextract.extract(request.form["swfUrl"]).subdomain
    db = dataset.connect(os.getenv('DATABASE_URL'))
    table = db['device']
    streamToken = request.form["name"]
    print(streamToken)
    print(subdomain)
    user = table.find_one(stream_token=streamToken)
    if not user:
        return 'invalid publish', 400

    print(f"stream started at {streamToken}")
    if user and user.get("publish_webhook"):
        invoke_webhook.delay(user.get("publish_webhook"), streamToken)

    return jsonify({"response" : "ok"})
 
@app.route('/resetToken/', methods=['POST'])
def reset():
    db = dataset.connect(os.getenv('DATABASE_URL'))
    table = db['device']
    stream_token = request.form["name"]
    print(stream_token)
    user = table.find_one(stream_token=stream_token)
    if user:
        table.update(dict(identifer=user["identifer"], stream_token=""), ["identifer"])
        print(f"stream ended at {stream_token}")
        if user.get("publish_end_webhook"):
            invoke_webhook.delay(user.get("publish_end_webhook"), stream_token)

    return jsonify({"response" : "ok"})

@app.route('/test1/', methods=['POST'])
def test1():
    print(request.form["stream_token"])
    return jsonify({"response" : "ok1"})

@app.route('/test2/', methods=['POST'])
def test2():
    print(request.form["stream_token"])
    return jsonify({"response" : "ok1"})

@celery.task()
def invoke_webhook(url, stream_token):
    print(f"webhook invoked {url}")
    print(requests.post(url, data={'stream_token': stream_token}))


@app.route('/cancel/', methods=['GET'])
def cancel_subscription():
    table = db['device']
    user = table.find_once(stripe_session_id=request.args.get('sessionId'))
    if user:
        stripe.Subscription.delete(user["subscription"])
        print(request.form["stream_token"])

    return render_template('cancel.html')

@celery.task()
def setup_streaming_instance(reference_id):
    db = dataset.connect(os.getenv('DATABASE_URL'))
    table = db['device']

    # # read zones
    client = LinodeClient(os.getenv('LINODE_TOKEN'))
    available_regions = client.regions()
    chosen_region = available_regions[0]

    image = [i.id for i in client.images(Image.label == os.getenv('LINODE_IMAGE_NAME'))][0]

    new_linode, password = client.linode.instance_create(os.getenv('LINODE_TYPE'),
                                                         chosen_region,
                                                         image=image)

    ip_address = new_linode.ipv4[0]

    # ip_address = "1.1.1.1"
    # password = "password"

    cf = CloudFlare.CloudFlare(token=os.getenv('CLOUDFLARE_READ_KEY'))
    zones = cf.zones.get(params={'name' : os.getenv('CLOUDFLARE_DOMAIN')})
    zone_id = None
    for zone in zones:
        zone_id = zone['id']
        zone_name = zone['name']

    subdomain = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    print(subdomain)
    dns_record = {
        'name': subdomain,
        'type': "A",
        'content': ip_address
    }

    # write zones
    cf2 = CloudFlare.CloudFlare(token=os.getenv('CLOUDFLARE_WRITE_KEY'))
    dns_record = cf2.zones.dns_records.post(zone_id, data=dns_record)

    print("ssh root@{} - {}".format(ip_address, password))

    table.update(dict(
        stripe_session_id=reference_id,
        linode_id=new_linode.id,
        ip_address=ip_address,
        password=password,
        subdomain=subdomain,
        zone_id=zone_id,
        publish_webhook="",
        publish_end_webhook="",
        stream_token=""), ["stripe_session_id"])

    user = table.find_one(stripe_session_id=reference_id)
    print(user)

    message = {
        'personalizations': [
            {
                'to': [
                    {
                        'email': user["email"]
                    }
                ],
                'subject': 'Video Streaming API Key'
            }
        ],
        'from': {
            'email': 'support@enterprisesworldwide.com'
        },
        'content': [
            {
                'type': 'text/html',
                'value': f'Below is your API key: <br/>api_key: {user["identifer"]}<br/><br/><a href="/cancel/?sessionId={user["stripe_session_id"]}">Cancel subscription</a>'
            }
        ]
    }

    api_key = os.environ.get('SENDGRID_API_KEY')

    try:
        sg = sendgrid.SendGridAPIClient(api_key)
        response = sg.send(message)
        print("email sent")
    except Exception as e:
        print(str(e))


if __name__ == '__main__':
    app.run(port=4242)
