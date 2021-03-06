import chainer.functions as F
import chainer.links as L
from chainer import Variable,optimizers,Chain
from chainer.links import caffe
from chainer import computational_graph as c
from deel.tensor import *
import copy

from deel import *
import chainer
import json
import os
import multiprocessing
import threading
import time
import six
import numpy as np
import os.path
from PIL import Image
from six.moves import queue
import pickle
import hashlib
import datetime
import sys
import random

__Model_cache={}

def getDim(shape):
	if not isinstance(shape,tuple):
		return shape
	dim=1
	for a in shape:
		dim *= a

	return dim

def LoadCaffeModel(path):
	print "Loading %s"%path
	root, ext = os.path.splitext(path)
	cashnpath = 'cash/'+hashlib.sha224(root).hexdigest()+".pkl"
	if path in __Model_cache:
		print "Cache hit"
		func = __Model_cache[path]
	if os.path.exists(cashnpath):
		func = pickle.load(open(cashnpath,'rb'))
	else:
		print "Converting from %s"%path
		func = caffe.CaffeFunction('misc/'+path)
		pickle.dump(func, open(cashnpath, 'wb'))
	__Model_cache[path]=func
	if Deel.gpu>=0:
		func = func.to_gpu(Deel.gpu)
	return func

class Network(object):
	t = None
	def __init__(self,name):
		self.name=name
		self.func=None
	def predict(self,x):
		'''
			Forward neural Network to prediction
		'''
		return None
	def classify(self,x=None):
		'''
			Classify x
		'''
		return None
	def trainer(self):
		'''
			Trainer for neural network
		'''
		return None
	def __str__(self):
		return self.name


def filter(image):
	cropwidth = 256 - ImageNet.in_size
	start = cropwidth // 2
	stop = start + ImageNet.in_size
	mean_image = ImageNet.mean_image[:, start:stop, start:stop].copy()
	target_shape = (256, 256)
	output_side_length=256

	xp = Deel.xp


	height, width, depth = image.shape
	new_height = output_side_length
	new_width = output_side_length
	if height > width:
		new_height = output_side_length * height / width
	else:
		new_width = output_side_length * width / height
	#resized_img = cv2.resize(image, (new_width, new_height))
	resized_img = Image.fromarray(xp.uint8(image.get()))
	#resized_img = copy.deepcopy(image)
	resized_img=resized_img.resize((new_width, new_height))
	resized_img=xp.asarray(resized_img)
	height_offset = (new_height - output_side_length) / 2
	width_offset = (new_width - output_side_length) / 2
	image= resized_img[height_offset:height_offset + output_side_length,
						width_offset:width_offset + output_side_length]

	image = image.transpose(2, 0, 1)
	image = image[:, start:stop, start:stop].astype(xp.float32)
	image -= mean_image



	return image

'''
	Perceptron
'''
class Perceptron(Chain,Network):
	def __init__(self,name="perceptron",layers=(1000,1000),optimizer=None,activation=F.sigmoid):
		Network.__init__(self,name)
		self.layers = {}
		for i in range(len(layers)-1):
			layer = L.Linear(layers[i],layers[i+1])
			self.layers['l'+str(i)]=layer
		self.model = Chain(**self.layers)
		if Deel.gpu >=0:
			self.model = self.model.to_gpu(Deel.gpu)
		self.optimizer = optimizers.MomentumSGD(lr=0.01,momentum=0.9)
		self.optimizer.setup(self.model)
		self.activation = activation
		

	def forward(self,x=None,t=None):
		if x is None:
			x=Tensor.context
		xp = Deel.xp

		volatile = 'off' if Deel.train else 'on'
		h = Variable(xp.asarray(x.value,dtype=xp.float32),volatile=volatile)

		self.optimizer.zero_grads()
		for i in range(len(self.layers)):
			h = F.dropout(self.activation(self.layers['l'+str(i)](h)),train=Deel.train)

		h = ChainerTensor(h)
		h.use()

		return h

	def backprop(self,t,x=None):
		if x is None:
			x=Tensor.context
		#loss = F.mean_squared_error(x.content,t.content)
		loss = F.softmax_cross_entropy(x.content,t.content)
		if  Deel.train:
			loss.backward()
		accuracy = F.accuracy(x.content,t.content)
		self.optimizer.update()
		return loss.data,accuracy.data
	


'''
	ImageNet
'''
class ImageNet(Network):
	mean_image=None
	in_size=224
	mean_image=None
	def __init__(self,name,in_size=224):
		super(ImageNet,self).__init__(name)
		ImageNet.in_size = in_size
	def Input(self,x):
		xp = Deel.xp
		if isinstance(x,str):
			img = Image.open(x)
			t = ImageTensor(img,filtered_image=filter(xp.asarray(img)),
							in_size=self.in_size)
		elif hasattr(x,'_Image__transformer'):
			t = ImageTensor(x,filtered_image=filter(xp.asarray(x)),
							in_size=self.in_size)
		else:
			t = ImageTensor(x,filtered_image=filter(xp.asarray(x)),
							in_size=self.in_size)
		t.use()
		return t
	def ShowLayers(self):
		for layer in self.func.layers:
			print layer[0],
			if hasattr(self.func,layer[0]):
				print self.func[layer[0]].W.data.shape
			else:
				print " "







import deel.model.lstm
class LSTM(Network):
	def __init__(self,optimizer=None,vocab=None,n_input_units=1000,
					n_units=650,grad_clip=5,bproplen=35):

		if vocab is None:
			vocab=BatchTrainer.vocab
		self.vocab=vocab
		n_vocab = len(vocab)
		super(LSTM,self).__init__('LSTM')

		self.func = model.lstm.RNNLM(n_input_units=n_input_units,n_vocab=n_vocab,n_units=n_units)
		self.func.compute_accuracy = False 
		for param in self.func.params():
			data = param.data
			data[:] = np.random.uniform(-0.1, 0.1, data.shape)


		if Deel.gpu>=0:
			self.func.to_gpu()


		if optimizer is None:
			self.optimizer = optimizers.SGD(lr=1.)
		self.optimizer.setup(self.func)
		self.clip = chainer.optimizer.GradientClipping(grad_clip)
		self.optimizer.add_hook(self.clip)

		self.accum_loss = 0
		self.cur_log_perp =  Deel.xp.zeros(())

	def evaluate(dataset):
		# Evaluation routine
		evaluator = self.func.copy()  # to use different state
		evaluator.predictor.reset_state()  # initialize state
		evaluator.predictor.train = False  # dropout does nothing

		sum_log_perp = 0
		for i in six.moves.range(dataset.size - 1):
			x = chainer.Variable(Deel.xp.asarray(dataset[i:i + 1]), volatile='on')
			t = chainer.Variable(Deel.xp.asarray(dataset[i + 1:i + 2]), volatile='on')
			loss = evaluator(x, t)
			sum_log_perp += loss.data
		return math.exp(float(sum_log_perp) / (dataset.size - 1))

	def forward(self,x=None):
		return self.func(x)

	def learn(self,str,x=None):
		if x is None:
			x=Tensor.context

		_t = Deel.xp.asarray([self.vocab[str[0]]], dtype=np.int32)
		t = ChainerTensor(Variable(_t))
		self.firstInput(t)

		
		for j in range(len(str)-2):
			_x = Deel.xp.asarray([self.vocab[str[j+1]]], dtype=np.int32)
			x = ChainerTensor(Variable(_x))
			x.use()

			_t = Deel.xp.asarray([self.vocab[str[j+2]]], dtype=np.int32)
			t = ChainerTensor(Variable(_t))
			self.train(t)
		

		return self.accum_loss.data



	def firstInput(self,t,x=None):
		if x is None:
			x=Tensor.context
		_x = x.content
		_t = t.content
		_y = self.func(_x,mode=1)
		loss = chainer.functions.loss.softmax_cross_entropy.softmax_cross_entropy(_y,_t)
		self.func.y = _y
		self.func.loss = loss
		self.accum_loss += loss
		self.cur_log_perp += loss.data

		return x

	def train(self,t,x=None):
		if x is None:
			x=Tensor.context
		_x = x.content
		_t = t.content

		_y = self.func(_x)
		loss= F.softmax_cross_entropy(_y,_t)
		self.func.y = _y
		self.func.loss = loss
		self.accum_loss += loss
		self.cur_log_perp += loss.data

		return self


	def backprop(self):
		self.func.zerograds()
		self.accum_loss.backward()
		self.accum_loss.unchain_backward()  # truncate
		self.accum_loss = 0
		self.optimizer.update()
		
import collections
def _sum_sqnorm(arr):
	sq_sum = collections.defaultdict(float)
	for x in arr:
		with cuda.get_device(x) as dev:
			x = x.ravel()
			s = x.dot(x)
			sq_sum[int(dev)] += s
	return sum([float(i) for i in six.itervalues(sq_sum)])

