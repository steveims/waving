import argparse
import csv
import os
import pathlib
import shutil
import sys


class Order():
    def __init__(self, ship_id):
        self.ship_id = ship_id
        self.items = {}  # sku: qty

    def add_item(self, sku, qty):
        # TODO: could a sku be repeated within an order?
        if sku not in self.items:
            self.items[sku] = 0
        self.items[sku] += qty

    def commit(self, boh, with_replen):
        can_commit = True
        for sku, qty in self.items.items():
            if sku not in boh or (boh[sku] < qty and not with_replen):
                can_commit = False

        if can_commit:
            for sku, qty in self.items.items():
                boh[sku] -= qty

        return can_commit

    def __repr__(self):
        return f'order {self.ship_id}: {self.items}'


def append_boh(row, boh, offset):
    slot = row[offset+4]
    if slot:
        qty = int(row[offset])
        boh[row[0]] = qty


# returns: list of boh dictionaries (sku: qty avail)
def load_boh(fname):
    bohs = [{}, {}, {}, {}]
    with open(fname, 'r') as f:
        reader = csv.reader(f)

        # skip headers: PRTNUM,Available Qty 1,Available Qty 3,Available Qty 4,Available Qty 6,LEVEL 1 LOCATION,LEVEL 3 LOCATION,LEVEL 4 LOCATION,LEVEL 6 LOCATION
        headers = next(reader)

        for row in reader:
            for i, boh in enumerate(bohs, start=1):
                append_boh(row, boh, i)

    return bohs


# returns: list of Order, sorted by ship_id
def load_orders(fname):
    orders_by_id = {}   # ship_id: Order
    with open(fname, 'r') as f:
        reader = csv.reader(f)

        # skip headers: SHIP_ID,PRTNUM,WAVE_SET,ORDQTY
        headers = next(reader)

        for row in reader:
            ship_id = row[0]
            order = orders_by_id.get(ship_id)
            if not order:
                order = Order(ship_id)
                orders_by_id[ship_id] = order
            order.add_item(row[1], int(row[3]))

    sorted_items = sorted(orders_by_id.items())
    sorted_orders = [x[1] for x in sorted_items]

    return sorted_orders


def check_orders(orders, boh):
    ready_orders = []
    replen_orders = []
    slot_orders = []
    sku_hist = {}  # sku: count

    retry_orders = []

    # check only ready-to-ship first
    for order in orders:
        if order.commit(boh, False):
            ready_orders.append(order)

        else:
            retry_orders.append(order)

    # of the remaining orders, check replen vs. slotting
    for order in retry_orders:
        if order.commit(boh, True):
            replen_orders.append(order)

        else:
            slot_orders.append(order)

            # tally the missing skus (candidates for slotting)
            for sku, qty in order.items.items():
                if sku not in boh:
                    count = sku_hist.get(sku, None)
                    if not count:
                        sku_hist[sku] = 0
                    sku_hist[sku] += qty

    # sort missing skus by count (descending) 
    ordered_skus = sorted(sku_hist.keys(), key=sku_hist.__getitem__, reverse=True)

    slot_results = []

    slot_candidates = []
    for sku in ordered_skus:
        slot_candidates.append(sku)

        # copy boh for each iteration to ensure same starting point
        trial_boh = dict(boh)
        for s in slot_candidates:
            trial_boh[s] = 0

        addn_orders = []
        for order in slot_orders:
            if order.commit(trial_boh, True):
                addn_orders.append(order)

        slot_results.append((list(slot_candidates), addn_orders, trial_boh))

    return ready_orders, replen_orders, slot_results


def cprint(c1, c2, c3, c4, c5):
    print(f'{c1: <12}{c2: >10}{c3: >10}{c4: >10}{c5: >10}')            

def cprint2(label, order_stats, f):
    cprint(label, safe(f, order_stats[0]), safe(f, order_stats[1]), safe(f, order_stats[2]), safe(f, order_stats[3]))

def safe(f, arg, alt='-'):
    try:
        return f(arg)

    except Exception:
        return alt    


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('wave_set')
    parser.add_argument('boh')
    parser.add_argument('-r', '--report', type=str, help='generate CSVs in specified directory')

    args = parser.parse_args()

    bohs = load_boh(args.boh)
    orders = load_orders(args.wave_set)

    order_stats = []
    for boh in bohs:
        order_stats.append(check_orders(orders, boh))


    cprint('Level', 1, 3, 4, 6)
    cprint('=====', '=', '=', '=', '=')
    cprint2('Ship now', order_stats, lambda x: len(x[0]))
    cprint2('With replen', order_stats, lambda x: len(x[1]))
    print('')
    cprint2('Missing SKUs', order_stats, lambda x: len(x[2]))
    print('')

    max_skus = 0
    for stats in order_stats:
        max_skus = max(max_skus, len(stats[2]))

    for i in range(max_skus):
        cprint2(f'+{i+1} SKU', order_stats, lambda x: len(x[2][i][1]))


    if args.report:
        if os.path.exists(args.report):
            print(f'WARNING: cannot generate reports because {args.report} already exists')
            sys.exit(1)

        else:
            levels = [1, 3, 4, 6]
            for i in range(4):
                level = levels[i]
                stats = order_stats[i]
                boh = bohs[i]
                base_dir = os.path.join(args.report, f'level_{level}')
                os.makedirs(base_dir)

                wave_set = list(stats[0])

                if wave_set:
                    with open(os.path.join(base_dir, f'{level}_ready_orders.csv'), 'w', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerow(['SHIP_ID', 'PRTNUM', 'WAVE_SET', 'ORDQTY'])
                        for order in wave_set:
                            for sku, qty in order.items.items():
                                writer.writerow([order.ship_id, sku, 'ECOM', qty])

                replen_orders = stats[1]
                wave_set.extend(replen_orders)
                if wave_set:
                    with open(os.path.join(base_dir, f'{level}_ready_replen_orders.csv'), 'w', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerow(['SHIP_ID', 'PRTNUM', 'WAVE_SET', 'ORDQTY'])
                        for order in wave_set:
                            for sku, qty in order.items.items():
                                writer.writerow([order.ship_id, sku, 'ECOM', qty])

                if replen_orders:
                    with open(os.path.join(base_dir, f'boh.csv'), 'w', newline='') as f:
                        writer = csv.writer(f)
                        writer.writerow(['PRTNUM', 'Qty'])
                        for sku, qty in boh.items():
                            writer.writerow([sku, qty])

                slots = stats[2]
                if slots:
                    for slot in slots:
                        slot_wave_set = list(wave_set)
                        slot_wave_set.extend(slot[1])
                        skus = slot[0]
                        slot_count = len(skus)
                        boh = slot[2]
                        slot_dir = os.path.join(base_dir, f'slot_{slot_count}')

                        os.mkdir(slot_dir)
                        with open(os.path.join(slot_dir, f'{level}_slot_{slot_count}_orders.csv'), 'w', newline='') as f:
                            writer = csv.writer(f)
                            writer.writerow(['SHIP_ID', 'PRTNUM', 'WAVE_SET', 'ORDQTY'])
                            for order in slot_wave_set:
                                for sku, qty in order.items.items():
                                    writer.writerow([order.ship_id, sku, 'ECOM', qty])

                        with open(os.path.join(slot_dir, 'skus.csv'), 'w', newline='') as f:
                            writer = csv.writer(f)
                            writer.writerow(['PRTNUM'])
                            for sku in skus:
                                writer.writerow([sku])

                        with open(os.path.join(slot_dir, 'boh.csv'), 'w', newline='') as f:
                            writer = csv.writer(f)
                            writer.writerow(['PRTNUM', 'Qty'])
                            for sku, qty in boh.items():
                                writer.writerow([sku, qty])


if __name__ == "__main__":
    main()

