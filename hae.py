#!/usr/bin/env python
# coding: utf-8

# In[1]:


# A BERT model with history answer embedding (HAE)


# In[2]:


# import os
# os.environ["CUDA_DEVICE_ORDER"]="PCI_BUS_ID"
# os.environ["CUDA_VISIBLE_DEVICES"]="3"


# In[3]:


from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import json
import math
import os
import modeling
import optimization
import tokenization
import six
import tensorflow as tf
import numpy as np
from copy import deepcopy
import pickle
import itertools
from time import time
from tqdm import tqdm

from cqa_supports import *
from cqa_flags import FLAGS

1


from cqa_model import *
from cqa_gen_batches import *

from scorer import external_call # quac official evaluation script


# In[4]:


for key in FLAGS:
    print(key, ':', FLAGS[key].value)

tf.set_random_seed(0)
tf.logging.set_verbosity(tf.logging.INFO)
bert_config = modeling.BertConfig.from_json_file(FLAGS.bert_config_file)

if FLAGS.max_seq_length > bert_config.max_position_embeddings:
    raise ValueError(
        "Cannot use sequence length %d because the BERT model "
        "was only trained up to sequence length %d" %
        (FLAGS.max_seq_length, bert_config.max_position_embeddings))

tf.gfile.MakeDirs(FLAGS.output_dir)
tf.gfile.MakeDirs(FLAGS.output_dir + '/summaries/train/')
tf.gfile.MakeDirs(FLAGS.output_dir + '/summaries/val/')
tf.gfile.MakeDirs(FLAGS.output_dir + '/summaries/rl/')

# files to write results
train_file_txt = open('train_file.txt','w') 
val_file_txt = open('val_file.txt','w')

tokenizer = tokenization.FullTokenizer(vocab_file=FLAGS.vocab_file, do_lower_case=FLAGS.do_lower_case)

if FLAGS.do_train:
    # read in training data, generate training features, and generate training batches
    train_examples = None
    num_train_steps = None
    num_warmup_steps = None
    train_file = FLAGS.quac_train_file
    train_examples = read_quac_examples(input_file=train_file, is_training=True)
        
    
    # we attempt to read features from cache
    features_fname = FLAGS.cache_dir + FLAGS.dataset.lower() +                '/train_features_{}_{}.pkl'.format(FLAGS.load_small_portion, FLAGS.max_considered_history_turns)
    example_tracker_fname = FLAGS.cache_dir + FLAGS.dataset.lower() +                '/example_tracker_{}_{}.pkl'.format(FLAGS.load_small_portion, FLAGS.max_considered_history_turns)
    variation_tracker_fname = FLAGS.cache_dir + FLAGS.dataset.lower() +                '/variation_tracker_{}_{}.pkl'.format(FLAGS.load_small_portion, FLAGS.max_considered_history_turns)
    example_features_nums_fname = FLAGS.cache_dir + FLAGS.dataset.lower() +                '/example_features_nums_{}_{}.pkl'.format(FLAGS.load_small_portion, FLAGS.max_considered_history_turns)
        
    try:
        print('attempting to load train features from cache')
        with open(features_fname, 'rb') as handle:
            train_features = pickle.load(handle)
        with open(example_tracker_fname, 'rb') as handle:
            example_tracker = pickle.load(handle)
        with open(variation_tracker_fname, 'rb') as handle:
            variation_tracker = pickle.load(handle)
        with open(example_features_nums_fname, 'rb') as handle:
            example_features_nums = pickle.load(handle)
    except:
        print('train feature cache does not exist, generating')
        convert_examples_to_variations_and_then_features(
                                        examples=train_examples, tokenizer=tokenizer, 
                                        max_seq_length=FLAGS.max_seq_length, doc_stride=FLAGS.doc_stride, 
                                        max_query_length=FLAGS.max_query_length, 
                                        max_considered_history_turns=FLAGS.max_considered_history_turns, 
                                        is_training=True,
                                        dir_='train')
        
        print('train features generated')
                
    
    num_train_steps = FLAGS.train_steps
    num_warmup_steps = int(num_train_steps * FLAGS.warmup_proportion)

if FLAGS.do_predict:
    # read in validation data, generate val features
    val_file = FLAGS.quac_predict_file
    val_examples = read_quac_examples(input_file=val_file, is_training=False)
    
    # we read in the val file in json for the external_call function in the validation step
    val_file_json = json.load(open(val_file, 'r'))['data']
    
    # we attempt to read features from cache
    features_fname = FLAGS.cache_dir + FLAGS.dataset.lower() +                      '/val_features_{}_{}.pkl'.format(FLAGS.load_small_portion, FLAGS.max_considered_history_turns)
    example_tracker_fname = FLAGS.cache_dir + FLAGS.dataset.lower() +                      '/val_example_tracker_{}_{}.pkl'.format(FLAGS.load_small_portion, FLAGS.max_considered_history_turns)
    variation_tracker_fname = FLAGS.cache_dir + FLAGS.dataset.lower() +                      '/val_variation_tracker_{}_{}.pkl'.format(FLAGS.load_small_portion, FLAGS.max_considered_history_turns)
    example_features_nums_fname = FLAGS.cache_dir + FLAGS.dataset.lower() +                      '/val_example_features_nums_{}_{}.pkl'.format(FLAGS.load_small_portion, FLAGS.max_considered_history_turns)
        
    try:
        print('attempting to load val features from cache')
        with open(features_fname, 'rb') as handle:
            val_features = pickle.load(handle)
        with open(example_tracker_fname, 'rb') as handle:
            val_example_tracker = pickle.load(handle)
        with open(variation_tracker_fname, 'rb') as handle:
            val_variation_tracker = pickle.load(handle)
        with open(example_features_nums_fname, 'rb') as handle:
            val_example_features_nums = pickle.load(handle)
    except:
        print('val feature cache does not exist, generating')
        convert_examples_to_variations_and_then_features(
                                                   examples=val_examples, tokenizer=tokenizer, 
                                                   max_seq_length=FLAGS.max_seq_length, doc_stride=FLAGS.doc_stride, 
                                                   max_query_length=FLAGS.max_query_length, 
                                                   max_considered_history_turns=FLAGS.max_considered_history_turns, 
                                                   is_training=False,
                                                   dir_='val')

        print('val features generated')
    
    
    num_val_examples = len(val_examples)
    

# tf Graph input
unique_ids = tf.placeholder(tf.int32, shape=[None], name='unique_ids')
input_ids = tf.placeholder(tf.int32, shape=[None, FLAGS.max_seq_length], name='input_ids')
input_mask = tf.placeholder(tf.int32, shape=[None, FLAGS.max_seq_length], name='input_mask')
segment_ids = tf.placeholder(tf.int32, shape=[None, FLAGS.max_seq_length], name='segment_ids')
start_positions = tf.placeholder(tf.int32, shape=[None], name='start_positions')
end_positions = tf.placeholder(tf.int32, shape=[None], name='end_positions')
history_answer_marker = tf.placeholder(tf.int32, shape=[None, FLAGS.max_seq_length], name='history_answer_marker')
training = tf.placeholder(tf.bool, name='training')
get_segment_rep = tf.placeholder(tf.bool, name='get_segment_rep')


bert_representation = bert_rep(
    bert_config=bert_config,
    is_training=training,
    input_ids=input_ids,
    input_mask=input_mask,
    segment_ids=segment_ids,
    history_answer_marker=history_answer_marker,
    use_one_hot_embeddings=False
    )
    
(start_logits, end_logits) = cqa_model(bert_representation)


tvars = tf.trainable_variables()

initialized_variable_names = {}
if FLAGS.init_checkpoint:
    (assignment_map, initialized_variable_names) = modeling.get_assigment_map_from_checkpoint(tvars, 
                                                                                              FLAGS.init_checkpoint)
    tf.train.init_from_checkpoint(FLAGS.init_checkpoint, assignment_map)

# compute loss
seq_length = modeling.get_shape_list(input_ids)[1]
def compute_loss(logits, positions):
    one_hot_positions = tf.one_hot(
        positions, depth=seq_length, dtype=tf.float32)
    log_probs = tf.nn.log_softmax(logits, axis=-1)
    loss = -tf.reduce_mean(tf.reduce_sum(one_hot_positions * log_probs, axis=-1))
    return loss

# get the max prob for the predicted start/end position
start_probs = tf.nn.softmax(start_logits, axis=-1)
start_prob = tf.reduce_max(start_probs, axis=-1)
end_probs = tf.nn.softmax(end_logits, axis=-1)
end_prob = tf.reduce_max(end_probs, axis=-1)

start_loss = compute_loss(start_logits, start_positions)
end_loss = compute_loss(end_logits, end_positions)
total_loss = (start_loss + end_loss) / 2.0
tf.summary.scalar('total_loss', total_loss)


if FLAGS.do_train:
    train_op = optimization.create_optimizer(total_loss, FLAGS.learning_rate, num_train_steps, num_warmup_steps, False)

    
merged_summary_op = tf.summary.merge_all()

RawResult = collections.namedtuple("RawResult", ["unique_id", "start_logits", "end_logits"])
have_checkpoint = False
saver = tf.train.Saver()
# Initializing the variables
init = tf.global_variables_initializer()
saver_sess = tf.train.Saver(max_to_keep=1)
if not have_checkpoint:
    tf.get_default_graph().finalize()
every_step_val = 3000
every_file_save = 15
epochs = 4

with tf.Session() as sess:
    if not have_checkpoint:
        sess.run(init)

    if FLAGS.do_train:
        train_summary_writer = tf.summary.FileWriter(FLAGS.output_dir + 'summaries/train', sess.graph)
        val_summary_writer = tf.summary.FileWriter(FLAGS.output_dir + 'summaries/val')
        
        f1_list = []
        heq_list = []
        dheq_list = []
        global_step = 1
        if have_checkpoint == True:
          saver = tf.train.import_meta_graph('gdrive/MyDrive/model_save/model.ckpt-195.meta')
          saver.restore(sess,tf.train.latest_checkpoint('gdrive/MyDrive/model_save/'))
        for epoch in range(epochs):
            print('################################## Epoch {} ##################################'.format(epoch))
            current_file_train = 1
            num_files_train = 3
            while current_file_train <= num_files_train:
                with open('data/train/all_features_{}'.format(current_file_train),'rb') as file_:
                    train_features = pickle.load(file_)
                with open('data/train/example_tracker_{}'.format(current_file_train),'rb') as file_:
                    example_tracker = pickle.load(file_)
                with open('data/train/variation_tracker_{}'.format(current_file_train),'rb') as file_:
                    variation_tracker = pickle.load(file_)
                with open('data/train/example_features_nums_{}'.format(current_file_train),'rb') as file_:
                    example_features_nums = pickle.load(file_)

                train_batches = cqa_gen_example_aware_batches(train_features, example_tracker, variation_tracker, 
                                              example_features_nums, FLAGS.train_batch_size, 
                                              FLAGS.num_train_epochs, shuffle=False)

                current_file_train += 1
                train_features = None
                example_tracker = None
                variation_tracker = None
                example_features_nums = None
                # Training cycle
                for step, batch in enumerate(train_batches):
    #                 if global_step > num_train_steps:
    #                     # this means the learning rate has been decayed to 0
    #                     print('break')
    #                     break

                    batch_features, batch_example_tracker, batch_variation_tracker = batch
                    batch = None

                    selected_example_features, relative_selected_pos = get_selected_example_features_without_actions(
                                                            batch_features, batch_example_tracker, batch_variation_tracker)

                    batch_features, batch_example_tracker, batch_variation_tracker = None, None, None

                    fd = convert_features_to_feed_dict(selected_example_features) # feed_dict
                    selected_example_features, relative_selected_pos = None, None
                    try:
                        _, train_summary, total_loss_res = sess.run([train_op, merged_summary_op, total_loss], 
                                                   feed_dict={unique_ids: fd['unique_ids'], input_ids: fd['input_ids'], 
                                                   input_mask: fd['input_mask'], segment_ids: fd['segment_ids'], 
                                                   start_positions: fd['start_positions'], end_positions: fd['end_positions'], 
                                                   history_answer_marker: fd['history_answer_marker'], training: True})
                    except:
                        print('could not')
    #                     print('features length: ', len(selected_example_features))

                    train_summary_writer.add_summary(train_summary, step)
                    train_summary_writer.flush()
                    train_file_txt.write('step {} | loss {}\n'.format(global_step,total_loss_res))
    #                 if global_step % 10 == 0:
    #                     print('training step: {}, total_loss: {}'.format(global_step, total_loss_res))

                    if global_step % every_step_val == 0:
                        print('################## prediction time ################## ')
                        val_total_loss = []
                        all_results = []
                        all_selected_examples = []
                        all_selected_features = []

                        total_num_selected = 0
                        total_num_actions = 0
                        total_num_examples = 0

                        current_file_val = 1
                        num_files_val = 3
                        while current_file_val <= num_files_val:
                            with open('data/val/all_features_{}'.format(current_file_val),'rb') as file_:
                                val_features = pickle.load(file_)
                            with open('data/val/example_tracker_{}'.format(current_file_val),'rb') as file_:
                                val_example_tracker = pickle.load(file_)
                            with open('data/val/variation_tracker_{}'.format(current_file_val),'rb') as file_:
                                val_variation_tracker = pickle.load(file_)
                            with open('data/val/example_features_nums_{}'.format(current_file_val),'rb') as file_:
                                val_example_features_nums = pickle.load(file_)

                            val_batches = cqa_gen_example_aware_batches(val_features, val_example_tracker, val_variation_tracker, 
                               val_example_features_nums, FLAGS.predict_batch_size, 1, shuffle=False)

                            current_file_val += 1

                            for val_batch in val_batches:
                                batch_results = []
                                batch_features, batch_example_tracker, batch_variation_tracker = val_batch

                                selected_example_features, relative_selected_pos = get_selected_example_features_without_actions(
                                                                batch_features, batch_example_tracker, batch_variation_tracker)


                                try:
                                    all_selected_features.extend(selected_example_features)

                                    fd = convert_features_to_feed_dict(selected_example_features) # feed_dict
                                    start_logits_res, end_logits_res, batch_total_loss = sess.run([start_logits, end_logits, total_loss], 
                                                feed_dict={unique_ids: fd['unique_ids'], input_ids: fd['input_ids'], 
                                                input_mask: fd['input_mask'], segment_ids: fd['segment_ids'], 
                                                start_positions: fd['start_positions'], end_positions: fd['end_positions'], 
                                                history_answer_marker: fd['history_answer_marker'], training: False})

                                    val_total_loss.append(batch_total_loss)

                                    for each_unique_id, each_start_logits, each_end_logits in zip(fd['unique_ids'], start_logits_res, 
                                                                                                  end_logits_res):  
                                        each_unique_id = int(each_unique_id)
                                        each_start_logits = [float(x) for x in each_start_logits.flat]
                                        each_end_logits = [float(x) for x in each_end_logits.flat]
                                        batch_results.append(RawResult(unique_id=each_unique_id, start_logits=each_start_logits, 
                                                                       end_logits=each_end_logits))

                                    all_results.extend(batch_results)
                                except:
                                    print('batch dropped because too large!')

                        output_prediction_file = os.path.join(FLAGS.output_dir, "predictions_{}.json".format(step))
                        output_nbest_file = os.path.join(FLAGS.output_dir, "nbest_predictions_{}.json".format(step))

                        write_predictions(val_examples, all_selected_features, all_results,
                                          FLAGS.n_best_size, FLAGS.max_answer_length,
                                          FLAGS.do_lower_case, output_prediction_file,
                                          output_nbest_file)

                        val_total_loss_value = np.average(val_total_loss)


                        # call the official evaluation script
                        val_summary = tf.Summary() 
                        val_eval_res = external_call(val_file_json, output_prediction_file)

                        val_f1 = val_eval_res['f1']
                        val_followup = val_eval_res['followup']
                        val_yesno = val_eval_res['yes/no']
                        val_heq = val_eval_res['HEQ']
                        val_dheq = val_eval_res['DHEQ']

                        heq_list.append(val_heq)
                        dheq_list.append(val_dheq)

    #                     val_summary.value.add(tag="followup", simple_value=val_followup)
    #                     val_summary.value.add(tag="val_yesno", simple_value=val_yesno)
    #                     val_summary.value.add(tag="val_heq", simple_value=val_heq)
    #                     val_summary.value.add(tag="val_dheq", simple_value=val_dheq)

                        print('evaluation: {}, total_loss: {}, f1: {}, followup: {}, yesno: {}, heq: {}, dheq: {}\n'.format(
                            step, val_total_loss_value, val_f1, val_followup, val_yesno, val_heq, val_dheq))
                        val_file_txt.write('step {} | f1 {}\n'.format(global_step,val_f1))
                        # with open(FLAGS.output_dir + 'step_result.txt', 'a') as fout:
                        #         fout.write('{},{},{},{},{},{}\n'.format(step, val_f1, val_heq, val_dheq, 
                        #                             FLAGS.history, FLAGS.output_dir))

                        # val_summary.value.add(tag="total_loss", simple_value=val_total_loss_value)
                        # val_summary.value.add(tag="f1", simple_value=val_f1)
                        # f1_list.append(val_f1)

                        # val_summary_writer.add_summary(val_summary, step)
                        # val_summary_writer.flush()

                        # save_path = saver.save(sess, '{}/model_{}.ckpt'.format(FLAGS.output_dir, step))
                        # print('Model saved in path', save_path)

                    all_results, all_selected_examples, all_selected_features, batch_features, batch_example_tracker, batch_variation_tracker, val_batches, val_features, val_example_tracker, val_variation_tracker, val_example_features_nums = None, None, None, None, None, None, None, None, None, None, None
                    global_step += 1

    #             if current_file_train % every_file_save == 0:
    #                 saver_sess.save(sess, "/content/gdrive/MyDrive/model_save/model.ckpt", global_step=current_file_train)
train_file_txt.close()
val_file_txt.close()

# In[5]:


# best_f1 = max(f1_list)
# best_f1_idx = f1_list.index(best_f1)
# best_heq = heq_list[best_f1_idx]
# best_dheq = dheq_list[best_f1_idx]
# with open(FLAGS.output_dir + 'result.txt', 'w') as fout:
#     fout.write('{},{},{},{},{}\n'.format(best_f1, best_heq, best_dheq, FLAGS.history, FLAGS.output_dir))


# In[ ]:




