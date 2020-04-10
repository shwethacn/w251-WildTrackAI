import tensorflow as tf
from tensorflow import keras
from tensorflow.keras.models import load_model
from tensorflow.keras.preprocessing import image
from tensorflow.keras.preprocessing.image import img_to_array
from tensorflow.keras.applications.vgg16 import preprocess_input as VGG16Pre
from tensorflow.keras.layers import Lambda, Flatten, Dense, Dropout
from tensorflow.keras.layers import Conv2D, ZeroPadding2D, Activation, Input, concatenate
from tensorflow.keras.models import Model
from tensorflow.keras import backend as K

import numpy as np
import pickle
import os
import paho.mqtt.client as mqtt
import time
import re
from datetime import datetime

modelpath='/WildAI/models'
printpath='/WildAI/data'


#Load Data
# Function to load and individual image to a specified size.
def load_image(image_file,preprocessor,size=(224,224)):
    try:
        img = image.load_img(image_file,target_size=size,interpolation="nearest")
    except:
        print("Failed to load ",image_file)
        return np.zeros(0)
    else:
        x= image.img_to_array(img)
        x = np.expand_dims(x, axis=0)
        x = preprocessor(x)
        x=np.squeeze(x)
    return x

def LoadDataSet(folder,preprocessor,size=(224,224)):
    Prints=[]
    Instances=[]
    Individuals=[]
    print("Loading New Data...")
    for instance in os.listdir(folder):
        print("\nCapture instance: ",instance)
        ind_path=folder+'/'+instance
        for footprint in os.listdir(ind_path):
            x=load_image(os.path.join(ind_path,footprint),preprocessor,(224,224))
            if x.shape[0]==0:
                continue
            else:
                print("Image: ",footprint,x.shape)
                Prints.append(x)

                np.save("/WildAI/images/arrays/"+footprint, x)
                #x.tofile("/WildAI/images/img"+footprint)                
                
                Instances.append(instance)
                Individuals.append(footprint)
    return Prints,Instances,Individuals


#Load SPecies list (for classification) and Individual Reference Embeddings (for identification)
with open(os.path.join(modelpath,'individuals_reference.pickle'), 'rb') as rhandle:
    rawref = pickle.load(rhandle)

with open(os.path.join(modelpath,'species_list.pickle'), 'rb') as fhandle:
    rawspecies = pickle.load(fhandle)


#Get Base saved model for species classification

vgg_model = load_model(os.path.join(modelpath,'species_classification_vgg16_model.h5'))


def getBase(modelpath,savedmodel='species_classification_vgg16_model.h5'):

  zero_model = load_model(os.path.join(modelpath,savedmodel))
  x=zero_model.get_layer('Embedding').output
  x = Lambda(lambda  x: K.l2_normalize(x,axis=1))(x)
  triplet_model=Model(inputs=zero_model.input,outputs=x)
  input_shape=[224,224,3]
  X=Input(input_shape)
  encoded = triplet_model(X)
  return X,encoded


def normalize(X):
    """
    function that normalizes each row of the matrix x to have unit length.

    """
    X=np.asarray(X)
    return X/np.linalg.norm(X, ord=2,keepdims=True)

def softmax(X):
    """Compute softmax values for each sets of scores in x."""
    X=np.asarray(X)
    Y=np.zeros(X.shape)
    for i in range(X.shape[0]):
      x=X[i]
      Y[i]=np.exp(x) / np.sum(np.exp(x), axis=0)
    return Y
    
def distance_probability(X):
    """Compute probability given a vector of distances"""
    X=np.asarray(X)
    Y=(1/X) / np.sum(1/X)
    return Y



#Encode images using model trained for individual Identification
def Encode(X,trained_model):
  num=len(X)
  X_encoded=[]
  #print("Total = ",num)
  for i in range(num):
    x=np.asarray(X[i]).reshape(-1,224,224,3)
    #print(i,species,x.shape)
    model=trained_model
    X_encoded.append(model.predict(x))
  return X_encoded

#FInd nearest matching individual in reference DB
def findnearest(Ref_Individuals,X):
  inds=[]
  dist=[]
  for individual,embedding in Ref_Individuals.items():
    inds.append(individual)
    dist.append(np.linalg.norm(X - embedding))
  norm_dist=normalize(dist)
  probs=distance_probability(norm_dist)
  i=np.argmin(np.asarray(dist))
  found=inds[i]
  #print("Distance: ",norm_dist)
  #print("Probability: ",probs)
  probability=probs[i]

  return found,probability




# For each image predict Species and then identify Individual


Prints,Instances,Files=LoadDataSet(printpath,VGG16Pre,(224,224))

# Predict Species Classification

print('\nPredicting Species for ',len(Prints),"captured footprints...\n")
X=np.asarray(Prints)

Y_logits=vgg_model.predict(X)

Y_probs=softmax(Y_logits)
Y=np.argmax(Y_probs,1)
Y_Species=[rawspecies[i] for i in Y]
Y_Probabilities=[""]*len(Y)

for cnt in range(len(Y)):
  Y_Probabilities[cnt]=100*Y_probs[cnt,Y[cnt]]

# Identify Individuals
print('\nIdentifying Individuals ......\n')

Y_Individuals=["" for i in range(len(Prints))]
Y_Ind_Probability=["" for i in range(len(Prints))]

for spec_indx in range(len(rawspecies)):
  
  species=rawspecies[spec_indx]

  tf.keras.backend.clear_session()
  inputs,outputs=getBase(modelpath,'species_classification_vgg16_model.h5')
  trained_model=Model(inputs=inputs,outputs=outputs)
  trained_model.compile(loss='binary_crossentropy', optimizer='adam', metrics=['accuracy'])
  fname='vgg16_best_model_'+str(species)+'.h5'
  trained_model.load_weights(os.path.join(modelpath,fname))

  indices=[i for i in range(len(Y)) if Y[i] == spec_indx]

  X_encoded=Encode([X[i] for i in indices],trained_model)
  
  num=len(X_encoded)
  for i in range(num):
    x=X_encoded[i]
    prediction,probability=findnearest(rawref[species],x.reshape(1,-1))
    Y_Ind_Probability[indices[i]]=probability*Y_Probabilities[indices[i]]
    Y_Individuals[indices[i]]=prediction
    
#Print out prediction results AND/OR compose message to be transmitted (see commented out code)

print("\n\nPreparing images for transfer...\n")

MQTT_HOST = "mqttBrok"
MQTT_PORT = 1883
QOS = 2

def on_connect(client, userdata, flags, rc):
    print("Connected to Edge Broker with RC:", rc)
    print("")

def on_publish(client, userdata, msgid):
    print("Message", msgid, "Published to Local Broker\n\n")

mqttclient = mqtt.Client("Edge Footprint Classifier")
mqttclient.on_connect = on_connect
mqttclient.on_publish = on_publish
mqttclient.connect(MQTT_HOST, MQTT_PORT, 60)
mqttclient.loop_start()
time.sleep(5)

print("\n\nIdentified the following footprints: \n")

for i in range(len(Prints)):

    mqttTopic = 'WildAI'
    dtStamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    device = 'TX2-MAR'
    fileName = Files[i]
    species = Y_Species[i]
    spcProb = str(round(Y_Probabilities[i],2))
    individual = Y_Individuals[i]
    indProb = str(round(Y_Ind_Probability[i],2))
    coordinates = Instances[i][2:Instances[i].rfind('_')]
  
    # print image attributes from inference
    print('{:^30}{:^1}{:^30}'.format('Attribute', '|', 'Value'))
    print('='*61)
    cols = '{:30}{:^1}{:>30}'
    print(cols.format('File Name', '|', fileName))
    print(cols.format('Device', '|', device))
    print(cols.format('Date, Time', '|', re.sub('_', ', ', dtStamp)))
    print(cols.format('Species (Prob)', '|', species + ' ('+spcProb+'%)'))
    print(cols.format('Individual (Prob)', '|', individual + ' ('+indProb+'%)'))
    print(cols.format('Coordinates (lat, lon)', '|', re.sub('_', ', ', coordinates)))

    # define mqtt message topic with relevant details
    msgTopic = mqttTopic+"/"+dtStamp+"/"+device+"/"+species+"/"+individual+"/"+indProb+"/"+coordinates
    # convert image numpy array to byte array for messaging
    img = pickle.dumps(Prints[i])
    # publish message to local mosquitto broker
    print("\nPublishing Message to Topic:", msgTopic)
    mqttclient.publish(msgTopic, payload=img, qos=QOS, retain=False)
    # set delay to allow time for message delivery
    time.sleep(5)

mqttclient.loop_stop()
mqttclient.disconnect()
