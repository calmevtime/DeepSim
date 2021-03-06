from deepSimNet import deepSimNet
import util
import tensorflow as tf
import numpy as np
import argparse
import os
import time

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gpu', default='0', type=str)
    parser.add_argument('--iters', default=200000, type=int)
    parser.add_argument('--imdb_name', default='voc_2012_trainval', type=str, help='dataset to train on')
    parser.add_argument('--batch_size', default=16, type=int)
    parser.add_argument('--logdir', type=str, required=True, help='log dir to store checkpoints and summary')
    parser.add_argument('--encoder', type=str, required=True, help='where is the encoder trained model')
    parser.add_argument('--seed', type=int, default=123456789)
    parser.add_argument('--optimizer', default='Adam', choices=['Adam', 'RMS'])
    parser.add_argument('--lr', type=float, default=0.002, help='start learning rate')
    parser.add_argument('--lrd', type=float, default=0.96, help='learning rate decay every 100k global step')
    parser.add_argument('--beta1', type=float, default=0.5, help='beta1 of optimizer')
    parser.add_argument('--save_freq', type=int, default=50000, help='save frequency')
    parser.add_argument('--show_freq', type=int, default=50, help='show frequency')
    parser.add_argument('--summ_freq', type=int, default=100, help='summary frequenc')
    parser.add_argument('--clip0', type=float, default=-0.05)
    parser.add_argument('--clip1', type=float, default=0.05)
    parser.add_argument('--critic_iters', type=int, default=5)
    parser.add_argument('--gan', choices=['wgan', 'lsgan', 'gan'], default='gan')
    parser.add_argument('--mode', default='train', choices=['train', 'test'])
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--recon_w', default=1, type=float)
    parser.add_argument('--feat_w', default=0.01, type=float)
    parser.add_argument('--dis_w', default=0.001, type=float)
    parser.add_argument('--manual', action='store_true')
    args = parser.parse_args()
    return args
args = parse_args()

def train():
    print(args)
    if not os.path.exists(args.logdir):
        os.makedirs(args.logdir)
    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu
    np.random.seed(args.seed)
    tf.set_random_seed(args.seed)
    with open(os.path.join(args.logdir, 'args'), 'w') as f:
        for k, v in vars(args).items():
            f.write(k+':'+str(v))

    net = deepSimNet(args.batch_size, args.recon_w, args.feat_w, args.dis_w, args.gan)
    data = util.DataFetcher(args.imdb_name)
    sess = tf.Session()
    
    # global step
    global_step = tf.contrib.framework.get_or_create_global_step()
    incr_global_step = tf.assign(global_step, global_step + 1)
    # learning rate after decay
    learning_rate = tf.train.exponential_decay(args.lr, global_step, 100000, args.lrd, staircase=True)
    # optimizer and train op
    if args.optimizer == 'RMS':
        G_optimizer = tf.train.RMSPropOptimizer(learning_rate, decay=args.beta1)
        D_optimizer = tf.train.RMSPropOptimizer(learning_rate*0.1, decay=args.beta1)
    elif args.optimizer == 'Adam':
        G_optimizer = tf.train.AdamOptimizer(learning_rate, beta1=args.beta1)
        D_optimizer = tf.train.AdamOptimizer(learning_rate*0.1, beta1=args.beta1)
    gen_grads = G_optimizer.compute_gradients(net.gen_loss, net.gen_variables)
    gen_train_op = G_optimizer.apply_gradients(gen_grads)
    dis_grads = D_optimizer.compute_gradients(net.dis_loss, net.dis_variables)
    dis_train_op = D_optimizer.apply_gradients(dis_grads)

    # clip op
    if args.gan == 'wgan':
        clip_disc_op = [var.assign(tf.clip_by_value(var, args.clip0, args.clip1)) for var in net.dis_variables]

    print('Initializing net, saver and tf...')
    sess.run(tf.global_variables_initializer())
    # restore the encoder model
    try:
        saver = tf.train.Saver(net.enc_variables)
        saver.restore(sess, tf.train.latest_checkpoint(args.encoder))
    except:
        raise Exception('fail to restore encoder. please check your encoder model')

    saver = tf.train.Saver(max_to_keep=None)
    ckpt = tf.train.get_checkpoint_state(args.logdir)
    if ckpt and ckpt.model_checkpoint_path:
        saver.restore(sess, ckpt.model_checkpoint_path)
        print('deepSimNet restored..')

    # summary information and handler
    for grad, var in gen_grads:
        tf.summary.histogram('generator/'+var.name+'/grad', grad)
        tf.summary.histogram('generator/'+var.name, var)
    for grad, var in dis_grads:
        tf.summary.histogram('discriminator/'+var.name+'/grad', grad)
        tf.summary.histogram('discriminator/'+var.name, var)
    tf.summary.scalar('gen_loss', net.gen_loss)
    tf.summary.scalar('dis_loss', net.dis_loss)
    tf.summary.scalar('G/gen_dis_loss', net.gen_dis_loss)
    tf.summary.scalar('G/recon_loss', net.recon_loss)
    tf.summary.scalar('G/feat_loss', net.feat_loss)
    tf.summary.scalar('real_score', tf.reduce_mean(net.real_score_logit))
    tf.summary.scalar('fake_score', tf.reduce_mean(net.fake_score_logit))
    tf.summary.image('real_image', util.bgr2rgb(util.invprep(net.real_image)))
    tf.summary.image('fake_image', util.bgr2rgb(util.invprep(net.fake_image)))
    summary_op = tf.summary.merge_all()
    summary_writer = tf.summary.FileWriter(args.logdir, sess.graph)

    # tf process initialization
    coord = tf.train.Coordinator()
    threads = tf.train.start_queue_runners(sess, coord)
    tic = time.time()
    try:
        for step in range(1, args.iters+1):
            tic_ = time.time()
            noise_sigma = 2/256.0*(1-step/500000.0)
            blobs = data.nextbatch(args.batch_size)
            feed_dict = {
                    net.original_image: blobs['data'],
                    net.noise_sigma: noise_sigma
                    }
            run_dict = {}
            run_dict['global_step'] = global_step
            run_dict['incr_global_step'] = incr_global_step
            run_dict['gen_train_op'] = gen_train_op

            if args.debug:
                run_dict['gen_grads'] = gen_grads
                run_dict['dis_grads'] = dis_grads
            if step % args.show_freq == 0:
                run_dict['dis_loss'] = net.dis_loss
                run_dict['gen_dis_loss'] = net.gen_dis_loss
                run_dict['recon_loss'] = net.recon_loss
                run_dict['feat_loss'] = net.feat_loss
            if step % args.summ_freq == 0:
                run_dict['summary'] = summary_op
            # run gen_train_op and other necessary information
            results = sess.run(run_dict, feed_dict=feed_dict)
            
            # run dis_train_op only
            if args.gan == 'wgan':
                for i in range(args.critic_iters): # for WGAN train
                    sess.run([dis_train_op, clip_disc_op], feed_dict=feed_dict)
                    blobs = data.nextbatch(args.batch_size)
                    feed_dict = {
                            net.original_image: blobs['data'],
                            net.noise_sigma: noise_sigma 
                            }
            elif args.gan == 'lsgan' or args.gan == 'gan':
                sess.run(dis_train_op, feed_dict=feed_dict)
            
            # save, summary and display
            if step % args.show_freq == 0:
                rate = step / (time.time() - tic)
                remaining = (args.iters+1-step) / rate
                print(' step %6d , dis_loss: %3f , gen_dis_loss: %3f , recon_loss: %3f , feat_loss: %3f, remaining %5dm' % (results['global_step'], results['dis_loss'], results['gen_dis_loss'], results['recon_loss'], results['feat_loss'], remaining/60)) 
            if step % args.save_freq == 0:
                print('================ saving model =================')
                saver.save(sess, os.path.join(args.logdir, 'model'), global_step=results['global_step'])
            if step % args.summ_freq == 0:
                print('-------------- recording summary --------------')
                summary_writer.add_summary(results['summary'], results['global_step'])
    except KeyboardInterrupt:
        print('End Training...')
    finally:
        coord.request_stop()
        coord.join(threads)


def test():
    pass

if __name__ == '__main__':
    if args.mode == 'train':
        train()

