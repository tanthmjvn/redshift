from fabric.api import local, run, lcd, cd, env
from fabric.operations import get, put
from fabric.contrib.files import exists
from pathlib import Path
import time
import re
from math import sqrt
from os.path import join as pjoin
from os import listdir
from StringIO import StringIO
import scipy.stats
import redshift.features

from itertools import combinations

env.use_ssh_config = True

from _paths import REMOTE_REPO, REMOTE_CONLL, REMOTE_MALT, REMOTE_STANFORD, REMOTE_PARSERS
from _paths import LOCAL_REPO, LOCAL_MALT, LOCAL_STANFORD, LOCAL_PARSERS
from _paths import HOSTS, GATEWAY

env.hosts = HOSTS
env.gateway = GATEWAY


def recompile(runner=local):
    clean()
    make()

def clean():
    with lcd(str(LOCAL_REPO)):
        local('python setup.py clean --all')

def make():
    with lcd(str(LOCAL_REPO)):
        local('python setup.py build_ext --inplace')

def qstat():
    run("qstat -na | grep mhonn")


def deploy():
    clean()
    make()
    with cd(str(REMOTE_REPO)):
        run('git pull')


def test1k(model="baseline", dbg=False):
    with lcd(str(LOCAL_REPO)):
        local(_train('~/work_data/stanford/1k_train.txt',  '~/work_data/parsers/tmp',
                    debug=dbg))
        local(_parse('~/work_data/parsers/tmp', '~/work_data/stanford/dev_auto_pos.parse',
                     '/tmp/parse', gold=True))


def draxx_baseline(name):
    model = pjoin(str(REMOTE_PARSERS), name)
    data = str(REMOTE_STANFORD)
    repo = str(REMOTE_REPO)
    train_str = _train(pjoin(data, 'train.txt'), model)
    parse_str = _parse(model, pjoin(data, 'devi.txt'), pjoin(model, 'dev'))
    eval_str = _evaluate(pjoin(model, 'dev', 'parses'), pjoin(data, 'devr.txt'))
    script = _pbsify(repo, [train_str, parse_str, eval_str])
    script_loc = pjoin(repo, 'pbs', '%s_draxx_baseline.pbs' % name)
    with cd(repo):
        put(StringIO(script), script_loc)
        run('qsub -N %s_bl %s' % (name, script_loc))


def k_iters(basename, k=5, movebeam=False):
    k = int(k)
    movebeam = bool(movebeam)
    for i in [5, 7, 10, 12, 15, 17, 20]:
        name = '%s_k%d_i%d' % (basename, k, i)
        if movebeam:
            name += 'm'
        draxx_beam(name, k=k, i=i, movebeam=movebeam, upd="max", alg="static")


def draxx_repair(name, extra_feats='False', repairs='True', k=0):
    extra_feats = True if extra_feats == 'True' else False
    repairs = True if repairs == 'True' else False
    k = int(k)
    if extra_feats:
        name += '_x'
    if not repairs:
        name += '_bl'
    if k != 0:
        name += '_k%d' % k
    print name
    data = str(REMOTE_STANFORD)
    repo = str(REMOTE_REPO)
    model_dir = pjoin(str(REMOTE_PARSERS), name)
    repair_str = '-r -d' if repairs else ''
    if repairs:
        train_alg = 'online'
        upd = 'cost'
    elif k == 0:
        train_alg = 'online'
        upd = 'cost'
    else:
        train_alg = 'static'
        upd = 'max'
    try:
        run('mkdir %s' % model_dir)
    except:
        pass
    for i in range(20):
        model = pjoin(model_dir, str(i))
        train_str = _train(pjoin(data, 'train.txt'), model, k=k, i=15,
                           add_feats=extra_feats, train_alg=train_alg,
                           args=repair_str, upd=upd, seed=i)
        parse_str = _parse(model, pjoin(data, 'devi.txt'), pjoin(model, 'dev'), k=k)
        eval_str = _evaluate(pjoin(model, 'dev', 'parses'), pjoin(data, 'devr.txt'))
        script = _pbsify(repo, [train_str, parse_str, eval_str])
        script_loc = pjoin(repo, 'pbs', '%s.pbs' % name)
        with cd(repo):
            put(StringIO(script), script_loc)
            run('qsub -N %s_%d %s' % (name, i, script_loc))


def draxx_beam(name, model=None, k=5, i=10, add_feats='False', upd='early', alg='static',
              movebeam='False', train_size="train.txt"):
    movebeam = True if movebeam == 'True' else False
    add_feats = True if add_feats == 'True' else False
    if name is not None:
        assert model is None
        model = pjoin(str(REMOTE_PARSERS), name)
    else:
        pieces = model.split('/')
        name = '%s_%s' % (pieces[-2], pieces[-1])
    data = str(REMOTE_STANFORD)
    repo = str(REMOTE_REPO)
    train_str = _train(pjoin(data, train_size), model, k=int(k), i=int(i),
                             add_feats=bool(add_feats), upd=upd, train_alg=alg,
                             movebeam=bool(movebeam))
    parse_str = _parse(model, pjoin(data, 'devi.txt'), pjoin(model, 'dev'), k=k)
    eval_str = _evaluate(pjoin(model, 'dev', 'parses'), pjoin(data, 'devr.txt'))
    script = _pbsify(repo, [train_str, parse_str, eval_str])
    script_loc = pjoin(repo, 'pbs', '%s_draxx_baseline.pbs' % name)
    with cd(repo):
        put(StringIO(script), script_loc)
        run('qsub -N %s_bl %s' % (name, script_loc))

def beam_isolate(name, size="1k_train.txt"):
    work_dir = pjoin(str(REMOTE_PARSERS), name)
    with cd(str(REMOTE_PARSERS)):
        for n in ['baseline', 'max_violation', 'feat_viol']:
            d = pjoin(work_dir, n)
            if not exists(d):
                run('mkdir -p %s' % d)
    #i_vals = [5, 10, 15, 30]
    #k_vals = [5, 10, 15, 30]
    i_vals = [10, 15, 30, 70]
    k_vals = [5, 10, 25]
    for i_val in i_vals:
        for k_val in k_vals:
            exp_dir = pjoin(work_dir, 'baseline', 'k%d_i%d' % (k_val, i_val))
            # Baseline
            #draxx_beam(None, model=exp_dir, k=k_val, i=i_val, add_feats=False, upd="early",
            #           alg="static", movebeam=False, train_size=size)
            # BL w/ Feats
            #exp_dir = pjoin(work_dir, 'features', 'k%d_i%d' % (k_val, i_val))
            #draxx_beam(None, model=exp_dir, k=k_val, i=i_val, add_feats=True, upd="early",
            #           alg="static", movebeam=False, train_size=size)
            # BL w/ Max-violation training
            exp_dir = pjoin(work_dir, 'max_violation', 'k%d_i%d' % (k_val, i_val))
            draxx_beam(None, model=exp_dir, k=k_val, i=i_val, add_feats=False, upd="max",
                       alg="static", movebeam=False, train_size=size)
            # w/ online
            #exp_dir = pjoin(work_dir, 'dynamic_oracle', 'k%d_i%d' % (k_val, i_val))
            #draxx_beam(None, model=exp_dir, k=k_val, i=i_val, add_feats=False, upd="early",
            #           alg="online", movebeam=False, train_size=size)
            # w/ movebeam
            #exp_dir = pjoin(work_dir, 'moves_in_beam', 'k%d_i%d' % (k_val, i_val))
            #draxx_beam(None, model=exp_dir, k=k_val, i=i_val, add_feats=False, upd="early",
            #           alg="static", movebeam=True, train_size=size)
            # w/ all
            exp_dir = pjoin(work_dir, 'feat_viol', 'k%d_i%d' % (k_val, i_val))
            draxx_beam(None, model=exp_dir, k=k_val, i=i_val, add_feats=True, upd="max",
                       alg="static", movebeam=False, train_size=size)


def beam_table(name):
    systems = ['baseline', 'features', 'max_violation', 'dynamic_oracle',
             'moves_in_beam', 'combined']
    table_names = ['Baseline', 'Features', 'Max. Violation', 'Dynamic Oracle', 'Prefer Moves', 'Combined']
    i_vals = [5, 10, 15, 30, 45]
    k_vals = [5, 10, 15, 25]
    print r"\documentclass{article}"
    print r"\begin{document}"
 
    print r"\begin{table}\begin{tabular}{l|%s}" % ('r' * len(i_vals))
    for k in k_vals:
        print r"\hline \hline \multicolumn{%s}{c}{ k = %s}  \\ \hline" % (len(i_vals), k)
        print ' & ' + ' & '.join(str(i) for i in i_vals) + r'\\'
        print r"\hline"
        for tn, system in zip(table_names, systems):
            print tn + '\t&\t',
            sys_dir = pjoin(str(REMOTE_PARSERS), name, system)
            accs = []
            for i in i_vals:
                exp_dir = pjoin(sys_dir, 'k%d_i%d' % (k, i))
                with cd(exp_dir):
                    if not exists(exp_dir):
                        accs.append(0)
                        continue
                    try:
                        text = run("cat dev/acc", quiet=True).stdout
                        accs.append(_get_acc(text, score='U'))
                    except:
                        accs.append(0)
                        continue
            print ' & '.join(fmt_pc(a) for a in accs) + r'\\'
            print "\hline"
    print r"\end{tabular}\end{table}"
    print r"\end{document}"

def conll_table(name):
    langs = ['arabic', 'basque', 'catalan', 'chinese', 'czech', 'english',
            'greek', 'hungarian', 'italian', 'turkish']
    systems = ['bl', 'exp']
    for lang in langs:
        bl_accs = []
        exp_accs = []
        for system, accs in zip(systems, ([bl_accs, exp_accs])):

            for i in range(20):
                uas_loc = pjoin(str(REMOTE_PARSERS), 'conll', lang, system,
                                str(i), 'dev', 'acc')
                try:
                    text = run('cat %s' % uas_loc, quiet=True).stdout
                    accs.append(_get_acc(text, score='U'))
                except:
                    continue
        if bl_accs:
            bl_n, bl_acc, stdev = _get_stdev(bl_accs)
        if exp_accs:
            exp_n, exp_acc, stdev = _get_stdev(exp_accs)
        if bl_n == exp_n:
            z, p = scipy.stats.wilcoxon(bl_accs, exp_accs)
        else:
            p = 1.0

        print lang, fmt_pc(bl_acc), fmt_pc(exp_acc), '%.4f' % p

def fmt_pc(pc):
    if pc < 1:
        pc *= 100
    return '%.2f' % pc


def conll(name, lang, n=20, debug=False):
    """Run the 20 seeds for the baseline and experiment conditions for a conll lang"""
    data = str(REMOTE_CONLL)
    repo = str(REMOTE_REPO)
    eval_pos = '%s.test.pos' % lang
    eval_parse = '%s.test.malt' % lang
    train_name = '%s.train.proj.malt' % lang
    n = int(n)
    if debug == True: n = 2
    for condition, arg_str in [('bl', ''), ('exp', '-r -d')]:
        for i in range(n):
            exp_name = '%s_%s_%s_%d' % (name, lang, condition, i)
            model = pjoin(str(REMOTE_PARSERS), name, lang, condition, str(i))
            run("mkdir -p %s" % model)
            train_str = _train(pjoin(data, train_name), model, k=0, i=15,
                               add_feats=False, train_alg='online', seed=i, label="conll",
                               args=arg_str)
            parse_str = _parse(model, pjoin(data, eval_pos), pjoin(model, 'dev'), k=0)
            eval_str = _evaluate(pjoin(model, 'dev', 'parses'), pjoin(data, eval_parse))
            grep_str = "grep 'U:' %s >> %s" % (pjoin(model, 'dev', 'acc'),
                                               pjoin(model, 'dev', 'uas')) 
            script = _pbsify(repo, (train_str, parse_str, eval_str, grep_str))
            if debug:
                print script
                continue
            script_loc = pjoin(repo, 'pbs', exp_name)
            with cd(repo):
                put(StringIO(script), script_loc)
                run('qsub -N %s_bl %s' % (exp_name, script_loc))
 

def ngram_add1(name, k=4, n=1, size=10000):
    n = int(n)
    k = int(k)
    size = int(size)
    data = str(REMOTE_MALT)
    repo = str(REMOTE_REPO)
    train_name = 'train.txt'
    eval_pos = 'devi.txt' 
    eval_parse = 'devr.txt'
    arg_str = 'base'
    train_n(n, 'base', pjoin(str(REMOTE_PARSERS), name), data, k=k, i=15,
            add_feats=True, train_alg='max', label="NONE", n_sents=size,
            ngrams="base")
    tokens = 's0,n0,n1,n2,n0l,n0l2,s0h,s0h2,s0r,s0r2,s0l,s0l2'.split(',')
    ngrams = ['%s_%s' % (p) for p in combinations(tokens, 2)]
    ngrams.extend('%s_%s_%s' % (p) for p in combinations(tokens, 3))
    n_ngrams = len(ngrams)
    for ngram_id, ngram_name in enumerate(ngrams[:10]):
        train_n(n, '%d_%s' % (ngram_id, ngram_name), pjoin(str(REMOTE_PARSERS), name),
                data, k=k, i=15, add_feats=True, train_alg='max', label="NONE",
                n_sents=size, ngrams='in%d' % ngram_id)
        break



def combine_ngrams(name, k=4, n=1, size=10000):
    base_accs = get_acc('base')
    for ngram_id in range(n_ngrams):
        accs = get_acc('in%d' % ngram_id)
        z, p = scipy.stats.wilcoxon(base_accs, accs)
        if p < p_thresh:
            accs.append((sum(accs) / len(accs), ngram_id))
    accs.sort()
    accs.reverse()
    while accs:
        for i in range(batch_size):
            acc, ngram_id = accs.pop()
            if not accs:
                break
        


def bigram_table(name):
    base_accs = []
    arg_str = 'base'
    for seed in range(20):
        exp_name = '%s_%s_%d' % (name, arg_str, seed)
        model = pjoin(str(REMOTE_PARSERS), name, arg_str, str(seed))
        uas_loc = pjoin(model, 'dev', 'acc')
        try:
            text = run('cat %s' % uas_loc, quiet=True).stdout
            base_accs.append(_get_acc(text, score='U'))
        except:
            continue
    good_bigrams = []
    s = 's0,n0,n1,n2,n0l,n0l2,s0h,s0h2,s0r,s0r2,s0l,s0l2'.split(',')
    names = ['%s_%s' % (p) for p in combinations(s, 2)]
    n, acc, stdev = _get_stdev(base_accs)
    print n, arg_str, fmt_pc(acc), '%.4f' % stdev
    base_acc = acc
    for bigram_id in range(66):
        arg_str = 'in%d' % bigram_id
        accs = []
        for seed in range(20):

            exp_name = '%s_%s_%d' % (name, arg_str, seed)
            model = pjoin(str(REMOTE_PARSERS), name, arg_str, str(seed))
            uas_loc = pjoin(model, 'dev', 'acc')
            try:
                text = run('cat %s' % uas_loc, quiet=True).stdout
                accs.append(_get_acc(text, score='U'))
            except:
                continue
        n, acc, stdev = _get_stdev(accs)
        z, p = scipy.stats.wilcoxon(base_accs, accs)
        print bigram_id, names[bigram_id], fmt_pc(acc), '%.4f' % p
        if acc > base_acc and p < 0.1:
            good_bigrams.append(bigram_id)
    print ','.join([str(i) for i in good_bigrams])
    print ','.join(names[bigram_id] for bigram_id in good_bigrams)
    print len(good_bigrams)
 

def train_n(n, name, exp_dir, data, k=1, add_feats=False, i=15, upd='early',
            movebeam=False, train_alg="online", label="Stanford", n_sents=0,
            ngrams="best_bi"):
    repo = str(REMOTE_REPO)
    for seed in range(n):
        exp_name = '%s_%d' % (name, seed)
        model = pjoin(exp_dir, name, str(seed))
        run("mkdir -p %s" % model)
        train_str = _train(pjoin(data, 'train.txt'), model, k=k, i=15,
                           add_feats=add_feats, train_alg=train_alg, seed=seed,
                           label=label, n_sents=n_sents, ngrams=ngrams)
        parse_str = _parse(model, pjoin(data, 'devi.txt'), pjoin(model, 'dev'), k=k)
        eval_str = _evaluate(pjoin(model, 'dev', 'parses'), pjoin(data, 'devr.txt'))
        grep_str = "grep 'U:' %s >> %s" % (pjoin(model, 'dev', 'acc'),
                                           pjoin(model, 'dev', 'uas')) 
        script = _pbsify(repo, (train_str, parse_str, eval_str, grep_str))
        script_loc = pjoin(repo, 'pbs', exp_name)
        with cd(repo):
            put(StringIO(script), script_loc)
            run('qsub -N %s %s' % (exp_name, script_loc))



def _train(data, model, debug=False, k=1, add_feats=False, i=15, upd='early',
           movebeam=False, train_alg="online", seed=0, args='', label="Stanford",
           n_sents=0, ngrams="best_bi"):
    feat_str = '-x' if add_feats else ''
    move_str = '-m' if movebeam else ''
    template = './scripts/train.py -i {i} -a {alg} -k {k} {movebeam} {feat_str} {data} {model} -s {seed} -l {label} -n {n_sents} -b {ngrams} {args}'
    if debug:
        template += ' -debug'
    return template.format(data=data, model=model, k=k, feat_str=feat_str, i=i,
                          upd=upd, movebeam=move_str, alg=train_alg, seed=seed,
                          label=label, args=args, n_sents=n_sents, ngrams=ngrams)


def _parse(model, data, out, gold=False, k=1):
    template = './scripts/parse.py -k {k} {model} {data} {out} '
    if gold:
        template += '-g'
    return template.format(model=model, data=data, out=out, k=k)


def _evaluate(test, gold):
    return './scripts/evaluate.py %s %s > %s' % (test, gold, test.replace('parses', 'acc'))


def _pbsify(repo, command_strs):
    header = """#! /bin/bash
#PBS -l walltime=20:00:00,mem=4gb,nodes=1:ppn=3
source /home/mhonniba/py27/bin/activate
export PYTHONPATH={repo}:{repo}/redshift:{repo}/svm
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/local/lib64:/lib64:/usr/lib64/:/usr/lib64/atlas:{repo}/redshift/svm/lib/
cd {repo}"""
    return header.format(repo=repo) + '\n' + '\n'.join(command_strs)




def avg_accs(exp_name, test=False):
    exp_dir = pjoin(str(REMOTE_PARSERS), exp_name)
    if test:
        exps = ['baseline', 'both']
    else:
        exps = ['baseline', 'reattach', 'adduce', 'both']
    uas_results = {}
    las_results = {}
    with cd(exp_dir):
        for system in exps:
            sys_dir = pjoin(exp_dir, system)
            uas = []
            las = []
            samples = run("ls %s" % sys_dir).split()
            for sample in samples:
                if sample == 'logs':
                    continue
                sample = Path(sys_dir).join(sample)
                if test:
                    acc_loc = sample.join('test').join('acc')
                else:
                    acc_loc = sample.join('dev').join('acc')
                try:
                    text = run("cat %s" % acc_loc).stdout
                except:
                    continue
                uas.append(_get_acc(text, score='U'))
                las.append(_get_acc(text, score='L'))
            uas_results[system] = _get_stdev(uas)
            las_results[system] = _get_stdev(las)
    return uas_results, las_results

def dev_eval_online(exp_name):
    uas_results, las_results = avg_accs(exp_name)
    print "UAS"
    for name, (n, mean, stdev) in uas_results.items():
        print '%s %d: %.1f +/- %.2f' % (name, n, mean, stdev)
    print "LAS"
    for name, (n, mean, stdev) in las_results.items():
        print '%s %d: %.1f +/- %.2f' % (name, n, mean, stdev)


def eval_online(exp_name):
    uas_results, las_results = avg_accs(exp_name, test=True)
    print "UAS"
    for name, (n, mean, stdev) in uas_results.items():
        print '%s %d: %.1f +/- %.2f' % (name, n, mean, stdev)
    print "LAS"
    for name, (n, mean, stdev) in las_results.items():
        print '%s %d: %.1f +/- %.2f' % (name, n, mean, stdev)




uas_re = re.compile(r'U: (\d\d.\d+)')
las_re = re.compile(r'L: (\d\d.\d+)')
# TODO: Hook up LAS arg
def _get_acc(text, score='U'):
    if score == 'U':
        return float(uas_re.search(text).groups()[0])
    else:
        return float(las_re.search(text).groups()[0])


def _get_stdev(scores):
    n = len(scores)
    mean = sum(scores) / n
    var = sum((s - mean)**2 for s in scores)/n
    return n, mean, sqrt(var)

def _get_repair_str(reattach, lower, invert):
    repair_str = []
    if reattach:
        repair_str.append('-r -o')
    if lower:
        repair_str.append('-w')
    if invert:
        repair_str.append('-v')
    return ' '.join(repair_str)


def _get_paths(here):
    if here == True:
        return LOCAL_REPO, LOCAL_STANFORD, LOCAL_PARSERS
    else:
        return REMOTE_REPO, REMOTE_STANFORD, REMOTE_PARSERS


def _get_train_name(data_loc, size):
    if size == 'full':
        train_name = 'train.txt'
    elif size == '1k':
        train_name = '1k_train.txt'
    elif size == '5k':
        train_name = '5k_train.txt'
    elif size == '10k':
        train_name = '10k_train.txt'
    else:
        raise StandardError(size)
    return data_loc.join(train_name)


def run_static(name, size='full', here=True, feats='all', labels="MALT", thresh=5, reattach=False,
              lower=False):
    train_name = _get_train_name(size)
    repair_str = ''
    if reattach:
        repair_str += '-r '
    if lower:
        repair_str += '-m'
    if feats == 'all':
        feats_flag = ''
    elif feats == 'zhang':
        feats_flag = '-x'
    if here is True:
        data_loc = Path(LOCAL_STANFORD)
        #if labels == 'Stanford':
        #    data_loc = Path(LOCAL_STANFORD)
        #else:
        #    data_loc = Path(LOCAL_CONLL)
        parser_loc = Path(LOCAL_PARSERS).join(name)
        runner = local
        cder = lcd
        repo = LOCAL_REPO
    else:
        if labels == 'Stanford':
            data_loc = Path(REMOTE_STANFORD)
        else:
            data_loc = Path(REMOTE_CONLL)
        parser_loc = Path(REMOTE_PARSERS).join(name)
        runner = run
        cder = cd
        repo = REMOTE_REPO

    train_loc = data_loc.join(train_name)
    with cder(repo):
        #runner('make -C redshift clean')
        runner('make -C redshift')
        if here is not True:
            arg_str = 'PARSER_DIR=%s,DATA_DIR=%s,FEATS="%s,LABELS=%s,THRESH=%s,REPAIRS=%s"' % (parser_loc, data_loc, feats_flag, labels, thresh, repair_str)
            job_name = 'redshift_%s' % name
            err_loc = parser_loc.join('err')
            out_loc = parser_loc.join('log')
            run('qsub -e %s -o %s -v %s -N %s pbs/redshift.pbs' % (err_loc, out_loc, arg_str, job_name))
            print "Waiting 2m for job to initialise"
            time.sleep(120)
            run('qstat -na | grep mhonniba')
            if err_loc.exists():
                print err_loc.open()

        else:
            dev_loc = data_loc.join('devr.txt')
            in_loc = data_loc.join('dev_auto_pos.parse')
            out_dir = parser_loc.join('parsed_dev')
            runner('./scripts/train.py %s -f %d -l %s %s %s %s' % (repair_str, thresh, labels, feats_flag, train_loc, parser_loc))
            runner('./scripts/parse.py -g %s %s %s' % (parser_loc, in_loc, out_dir))
            runner('./scripts/evaluate.py %s %s' % (out_dir.join('parses'), dev_loc)) 


