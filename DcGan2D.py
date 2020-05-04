import SimpleITK as sitk
import os
import tensorflow as tf
import pandas as pd
import cv2
from tqdm import tqdm

from matplotlib import pyplot as plt
from dltk.io.augmentation import *
from dltk.io.preprocessing import *

import glob
import imageio
import PIL
import time

def load_img(file_path, subject_id):
  x = []
  
  #  Construct a file path to read an image from.
  t1_img = os.path.join(file_path, '{}/{}_t1.nii.gz'.format(subject_id, subject_id))

  # Read the .nii image containing a brain volume with SimpleITK and get 
  # the numpy array:
  sitk_t1 = sitk.ReadImage(t1_img)
  t1 = sitk.GetArrayFromImage(sitk_t1)

  # Select the slices from 50 to 125 among the whole 155 slices to omit initial/final slices, 
  # since they convey a negligible amount of useful information and could affect training
  t1 = t1[50:125]
  
  # Resize images to 64 x 64 from 240 x 240
  t1_new = np.zeros((t1.shape[0], 64, 64))
  for i in range(t1.shape[0]):
    t1_new[i] = cv2.resize(t1[i], dsize=(64, 64), interpolation=cv2.INTER_CUBIC)

  # Normalise the image to zero mean/unit std dev:
  t1 = whitening(t1_new)
  
  # Create a 4D Tensor with a dummy dimension for channels
  t1 = np.moveaxis(t1, 0, -1)
  
  return t1

## copied from tensorflow site
def _float_feature(value):
  """Returns a float_list from a float / double."""
  return tf.train.Feature(float_list=tf.train.FloatList(value=[value]))

def _bytes_feature(value):
  """Returns a bytes_list from a string / byte."""
  if isinstance(value, type(tf.constant(0))):
    value = value.numpy() # BytesList won't unpack a string from an EagerTensor.
  return tf.train.Feature(bytes_list=tf.train.BytesList(value=[value]))

############

create_TF_RECORDS = False

if create_TF_RECORDS:
  # open the TFRecords file
  train_filename = '../train2d.tfrecords'


  writer = tf.io.TFRecordWriter(train_filename)

  # Iterate through directories from the training dataset
  dataset_path = '../data/'  # os.chdir(dataset_path)
  counter = 1
  for subject_id in tqdm(os.listdir(dataset_path)):

    # Load the image
    img = load_img(dataset_path, subject_id)

    # Create a feature
    feature = {'t1': _bytes_feature(tf.io.serialize_tensor(img, name=None))}
    
    # Create an example protocol buffer
    example = tf.train.Example(features=tf.train.Features(feature=feature))
    
    # Serialize to string and write on the file
    writer.write(example.SerializeToString())
    counter += 1

  writer.close()

###########

def _decode(example_proto):
  # Description of the features.
  feature_description = {'t1': tf.io.FixedLenFeature([], tf.string)}
  
  # Parse the input `tf.Example` proto using the dictionary above.
  features = tf.io.parse_single_example(example_proto, feature_description)

  img = tf.io.parse_tensor(features['t1'], out_type=tf.float32, name=None)
  return img

def parse_dataset(filename):
  raw_dataset = tf.data.TFRecordDataset(filename)
  return raw_dataset.map(_decode)









from tensorflow.keras.layers import (Conv2D,
                                     Dense,
                                     Conv2DTranspose,
                                     Reshape,
                                     BatchNormalization,
                                     LeakyReLU,
                                     Activation, 
                                     Flatten,
                                     Dropout)
from tensorflow.keras import Sequential
from tensorflow.keras.utils import Progbar

########################### DCGAN #########################################################

def generator2d(img_shape=(64, 64, 75),
                noise_shape = (100, ),
                kernel_size = (4, 4),
                strides = (2, 2),
                upsample_layers = 4,
                starting_filters = 512):

  filters = starting_filters
  starting_img_size = img_shape[1] //(2**upsample_layers)
  
  model = Sequential()
  model.add(Dense((starting_img_size ** 2) * starting_filters, 
                  input_shape=noise_shape, use_bias=False))
  model.add(BatchNormalization())
  model.add(LeakyReLU())

  model.add(Reshape((starting_img_size, starting_img_size,filters)))

  ## 3 Hidden Convolution Layers
  for l in range(upsample_layers-1):
    filters = int(filters / 2)
    model.add(Conv2DTranspose(filters, kernel_size, strides,
                              padding='same', use_bias=False))
    model.add(BatchNormalization())
    model.add(LeakyReLU())
  
  ## 4th Convolution Layer
  model.add(Conv2DTranspose(img_shape[-1], kernel_size, strides, 
                            padding='same', use_bias=False))
  return model


def discriminator2d(input_shape=(64, 64, 75),
                    kernel_size = (4, 4),
                    strides = (2, 2),
                    upsample_layers = 4):

  filters = input_shape[0]

  model = Sequential()
  model.add(Conv2D(strides=strides,
                  kernel_size = kernel_size,
                  filters = filters,
                  input_shape=input_shape,
                  padding='same'))
  
  model.add(LeakyReLU())
  model.add(Dropout(0.2))

  for l in range(upsample_layers-1):
    filters = int(filters * 2)
    model.add(Conv2D(strides=strides,
                     kernel_size = kernel_size,
                     filters = filters,
                     padding='same'))
    model.add(LeakyReLU())

  model.add(Flatten())
  model.add(Dense(1))
  return model


def generator_loss(fake_output):
    cross_entropy = tf.keras.losses.BinaryCrossentropy(from_logits=True)
    return cross_entropy(tf.ones_like(fake_output), fake_output)


def discriminator_loss(real_output, fake_output):
    cross_entropy = tf.keras.losses.BinaryCrossentropy(from_logits=True)
    real_loss = cross_entropy(tf.ones_like(real_output), real_output)
    fake_loss = cross_entropy(tf.zeros_like(fake_output), fake_output)
    total_loss = real_loss + fake_loss
    return total_loss


@tf.function(autograph=True)
def train_step(images):
    batch_size = int(len(images))
    noise = np.random.uniform(-1, 1, size=(batch_size, 100))

    with tf.GradientTape() as gen_tape, tf.GradientTape() as disc_tape:
      generated_images = generator(noise, training=True)

      real_output = discriminator(images, training=True)
      fake_output = discriminator(generated_images, training=True)

      gen_loss = generator_loss(fake_output)
      disc_loss = discriminator_loss(real_output, fake_output)

    gradients_of_generator = gen_tape.gradient(gen_loss, generator.trainable_variables)
    gradients_of_discriminator = disc_tape.gradient(disc_loss, discriminator.trainable_variables)

    generator_optimizer.apply_gradients(zip(gradients_of_generator, generator.trainable_variables))
    discriminator_optimizer.apply_gradients(zip(gradients_of_discriminator, discriminator.trainable_variables))

    return gen_loss, disc_loss



def train(dataset, num_training_samples, epochs, batch_size, save_dir_path):
  BUFFER_SIZE = 6000
  
  writer = tf.summary.create_file_writer(os.path.join(save_dir_path, 'logs'))

  checkpoint_dir = save_dir_path
  checkpoint_prefix = os.path.join("training_checkpoints", "ckpt")
  checkpoint = tf.train.Checkpoint(generator_optimizer=generator_optimizer,
                              discriminator_optimizer=discriminator_optimizer,
                              generator=generator,
                              discriminator=discriminator)
  #checkpoint.restore(checkpoint_dir)
  manager = tf.train.CheckpointManager(checkpoint,
                                      directory=checkpoint_dir,
                                      max_to_keep = 10,
                                      checkpoint_name=checkpoint_prefix)

  try:
#     latest = tf.train.latest_checkpoint('training_checkpoints')
#     checkpoint.restore(latest).assert_consumed()
    status = checkpoint.restore(manager.latest_checkpoint)
    restored_epoch_number = int(manager.latest_checkpoint.split("-")[-1])*5
  except:
    restored_epoch_number = 0
  print("restoring checkpoint {}".format(restored_epoch_number))
  dataset = dataset.shuffle(BUFFER_SIZE).batch(batch_size)
    
  #global_step = restored_epoch_number*batch_size
  # keep batch size the same when restoring checkpoints
  global_step = 0
  for epoch in range(restored_epoch_number, epochs):
    print("\nepoch {}/{}".format(epoch+1,epochs))
    pb_i = Progbar(target=num_training_samples, verbose=1)
    
    for image_batch in dataset.as_numpy_iterator():
      gen_loss, disc_loss = train_step(image_batch)
      time.sleep(0.3)
      pb_i.add(batch_size, values=[('gen_loss', gen_loss), ('disc_loss', disc_loss)])
     
      with writer.as_default():
          tf.summary.scalar('gen_loss', gen_loss, step=global_step)
          tf.summary.scalar('disc_loss', disc_loss, step=global_step)
          writer.flush()
      global_step+=1
    
    # Save the model every 5 epochs
    if (epoch + 1) % 5 == 0:
      manager.save()
    
      # Produce images for the GIF as we go
      test_noise = np.random.uniform(-1, 1, size=(1, 100))
      gen_and_save_images(generator, epoch + 1, test_noise, save_dir_path, False)

  # # Generate after the final epoch
  #display.clear_output(wait=True)
  test_noise = np.random.uniform(-1, 1, size=(1, 100))
  gen_and_save_images(generator, epoch + 1, test_noise, save_dir_path, False)


def gen_and_save_images(model, epoch, test_noise, save_dir_path, show=False):
  save_dir_path = os.path.join(save_dir_path, "images")
  if not os.path.exists(save_dir_path):
        os.mkdir(save_dir_path)
        
  preds = model(test_noise, training=False)
  fig = plt.figure(figsize=(10, 10))

  for ind, i in enumerate(range(20, 36)):
    plt.subplot(4, 4, ind+1)
    plt.imshow(preds[0][:,:,i], cmap='gray')
    plt.axis('off')
  plt.savefig(
      os.path.join(save_dir_path, 'img_epoch_{:04d}.png'.format(epoch)),
      bbox_inches='tight')
  if show:
    plt.show()
  plt.close()
    
################################################################################
## Load Dataset

train_filename = '../train2d.tfrecords'

parsed_dataset = parse_dataset(train_filename)
num_training_examples = sum(1 for _ in tf.data.TFRecordDataset(train_filename))

############################## Train #############################3

tf.keras.backend.clear_session()

model_name = time.strftime('%Y-%m-%d_%H:%M:%S')
if not os.path.exists(model_name):
  os.mkdir(model_name)
print("saving images in: {}".format(model_name))

########### Params #####################
disc_lr = 4e-4
gen_lr = 1e-4
noise_dim = 100
EPOCHS = 3000
BATCH_SIZE = 64

##################################3

generator=generator2d()
print(generator.summary())

discriminator=discriminator2d()
print(discriminator.summary())

generator_optimizer=tf.keras.optimizers.Adam(gen_lr)
discriminator_optimizer=tf.keras.optimizers.Adam(disc_lr)


train(dataset=parsed_dataset,
      num_training_samples = num_training_examples, 
      epochs=EPOCHS, 
      batch_size=BATCH_SIZE,
      save_dir_path = model_name)