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

@app.route('/about', methods=['GET'])
def get_example():
    return render_template('about.html')

@app.route('/publishable-key', methods=['GET'])
def get_publishable_key():
    return jsonify({'publishableKey': os.getenv('STRIPE_PUBLISHABLE_KEY')})

# Fetch the Checkout Session to display the JSON result on the success page
@app.route('/checkout-session', methods=['GET'])
def get_checkout_session():
    id = request.args.get('sessionId')
    checkout_session = stripe.checkout.Session.retrieve(id)
    return jsonify(checkout_session)


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
            cancel_url=domain_url + "/cancel.html",
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
        setup_streaming_instance.delay(data_object["client_reference_id"], customer.email)

        # items = data_object['display_items']
        # customer = stripe.Customer.retrieve(data_object['customer'])

        # if len(items) > 0 and items[0].custom and items[0].custom.name == 'Pasha e-book':
        #     print(
        #         '🔔 Customer is subscribed and bought an e-book! Send the e-book to ' + customer.email)
        # else:
        #     print(
        #         '🔔 Customer is subscribed but did not buy an e-book')

    return jsonify({'status': 'success'})

@app.route('/publish/', methods=['POST'])
def index():
    query = parse_qs(request.body.getvalue().decode('utf-8'))
    streamToken = query["name"][0]
    print(streamToken)
 
@app.route('/resetToken/', methods=['POST'])
def reset():
    query = parse_qs(request.body.getvalue().decode('utf-8'))
    streamToken = query["name"][0]
    print(streamToken)

@celery.task()
def setup_streaming_instance(reference_id, email):
    db = dataset.connect(os.getenv('DATABASE_URL'))

    table = db['device']

    # # read zones
    # client = LinodeClient(os.getenv('LINODE_TOKEN'))
    # available_regions = client.regions()
    # chosen_region = available_regions[0]

    # image = [i.id for i in client.images(Image.label == os.getenv('LINODE_IMAGE_NAME'))][0]

    # new_linode, password = client.linode.instance_create(os.getenv('LINODE_TYPE'),
    #                                                      chosen_region,
    #                                                      image=image)

    # ip_address = new_linode.ipv4[0]

    ip_address = "1.1.1.1"
    password = "password"

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

    # print("ssh root@{} - {}".format(ip_address, password))

    table.insert(dict(user_id=reference_id,
        email=email,
        linode_id="3", #new_linode.id
        ip_address=ip_address,
        password=password,
        subdomain=subdomain,
        zone_id=zone_id,
        identifer=str(uuid.uuid4()),
        stream_token=""))

    message = {
        'personalizations': [
            {
                'to': [
                    {
                        'email': email
                    }
                ],
                'subject': 'Sending with Twilio SendGrid is Fun'
            }
        ],
        'from': {
            'email': 'support@enterprisesworldwide.com'
        },
        'content': [
            {
                'type': 'text/html',
                'value': '<strong>and easy to do anywhere, even with Python</strong>'
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
