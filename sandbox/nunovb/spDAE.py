import tensorflow as tf
import math
import os

PATH = "/home/nvb/NNet/models/"

# Create model
class Autoencoder:
    def __init__(self, n_input=1024, n_hidden_1=1536, n_hidden_2=2048, rho=0.01, alpha=0.0001, beta=3):
        # n_hidden_1: 1st and 3rd layer number of neurons
        # n_hidden_2: 2nd(Latent) layer number of neurons
        # n_input: length of ECG window
        # Store layers weight & bias - try different weights for decoder layers
        self.rho = rho
        self.alpha = alpha
        self.beta = beta
        r1 = math.sqrt(6) / math.sqrt(n_input + n_hidden_1 + 1)
        r2 = math.sqrt(6) / math.sqrt(n_input + n_hidden_2 + 1)
        self.weights = {
            'h1': tf.Variable(tf.random_normal([n_input, n_hidden_1], stddev=r1)), #tf.initializers.orthogonal()([n_input, n_hidden_1]),
            'h2': tf.Variable(tf.random_normal([n_hidden_1, n_hidden_2], stddev=r2)),
            'h3': tf.Variable(tf.random_normal([n_hidden_2, n_hidden_1], stddev=r2)),
            'h4': tf.Variable(tf.random_normal([n_hidden_1, n_input], stddev=r1))}
        self.biases = {
            'b1': tf.Variable(tf.random_normal([n_hidden_1], stddev=r1)),
            'b2': tf.Variable(tf.random_normal([n_hidden_2], stddev=r2)),
            'b3': tf.Variable(tf.random_normal([n_hidden_1], stddev=r1)),
        }
        
        self.params = [self.weights, self.biases] #b_prime + W.T
    
    def kl_divergence(self, rho, rho_hat):
        return rho * tf.log(rho) - rho * tf.log(rho_hat) + (1 - rho) * tf.log(1 - rho) - (1 - rho) * tf.log(1 - rho_hat)

    def regularization(self,weights):
        return tf.nn.l2_loss(weights)

    def fit(self, x, n_epochs=15, learning_rate=0.01, batch_size=16, display_step=1, save=True, name='1', load=False):
        # tf Graph input
        X = tf.placeholder("float", [None, x.shape[1]])
        #Y = tf.placeholder("float", [None, n_classes])

        saver = tf.train.Saver()
        # Hidden fully connected layers
        # - Add sigmoid and try swish
        # - To add noise to x, replace x with x+noise_amt*tf.maximum(x)*tf.random_normal(x.shape)
        self.layer_1 = tf.nn.sigmoid(tf.add(tf.matmul(x, self.weights['h1']), self.biases['b1']))
        self.layer_2 = tf.nn.sigmoid(tf.add(tf.matmul(self.layer_1, self.weights['h2']), self.biases['b2']))
        self.layer_3 = tf.nn.sigmoid(tf.add(tf.matmul(self.layer_2, self.weights['h3']), self.biases['b3']))
        self.layer_4 = tf.matmul(self.layer_3, self.weights['h4'])#, self.biases['b4'])

        # Try to plot costs for different learning rates in order to optimize lr!!!
        self.costs = []

        # Define loss and optimizer
        rho_hat = tf.reduce_mean(self.layer_2,axis=0)
        kl = self.kl_divergence(self.rho, rho_hat + 0.01)
        
        cost = 0.5*tf.reduce_mean(tf.squared_difference(self.layer_4, x))+ \
                0.5*self.alpha*(tf.nn.l2_loss(self.weights['h1'])+ \
                tf.nn.l2_loss(self.weights['h2'])+tf.nn.l2_loss(self.weights['h3'])+ \
                tf.nn.l2_loss(self.weights['h4']))+self.beta*tf.reduce_sum(kl)
        optimizer = tf.train.AdamOptimizer(learning_rate).minimize(cost)
        
        # Initializing the variables
        init = tf.global_variables_initializer()
        
        self.sess = tf.Session() 
        self.sess.run(init)
        if load:
            #saver = tf.train.import_meta_graph(PATH + name + '.meta')
            saver.restore(self.sess, tf.train.latest_checkpoint(PATH + './'))#PATH + './' ))))
            print("Model {} Loaded".format(name))
        
        # Training cycle
        for epoch in range(n_epochs):
            avg_cost = 0
            total_batch = int(len(x)/batch_size)
            # Loop over all batches
            for i in range(total_batch):
                batch_x = x[i * batch_size: i * batch_size + batch_size]
                # Run optimization op (backprop) and cost (to get loss value)
                _, c = self.sess.run([optimizer, cost], feed_dict={X: batch_x})#Y: batch_y})
                #print("rho_hat:",self.sess.run(rho_hat))                                                
                # Compute average loss
                avg_cost += c / total_batch

            # Update cost vector
            self.costs.append(avg_cost)
            
            # Display logs per epoch step
            if epoch % display_step == 0:
                print("Epoch:", '%04d' % (epoch+1), "cost={:.9f}".format(avg_cost))
        print("Optimization Finished!")

        if save:
            # add subfolder for more than 1 trained model
            save_path = saver.save(self.sess, PATH + name)
            print("Model saved in file: %s" % PATH + name)

    def get_cost_vector(self):
        return self.sess.run(tf.cast(self.costs, dtype=tf.float32))
    
    def reconstruct(self, x_t):
        # Reconstructs the input signal
        X = tf.placeholder("float", x_t.shape)
        return self.sess.run(self.layer_4, feed_dict={X: x_t})
        
    def get_latent(self, x_t):
        # Returns the latent representation of the input signal
        X = tf.placeholder("float", x_t.shape)
        return self.sess.run(self.layer_2, feed_dict={X: x_t})
    
