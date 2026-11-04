"""Rule cases for the media / documents / RAG / misc slice: #219 #328 #347 #348 #350 #351 #384 #394 #396 #422 #507 #518 #613 #728
#729 #760 #765 #770 #1020 #1104 #1329 #1332.

Each is a RULE: the transform drops information on purpose, so `probes` pins the answers an agent must still be able to give
(every failed request, every non-vendored file, the words and stamps of a transcript, the top profile rows, ...) and `loses=`
names exactly what is gone. Probes are written as a different algorithm from the encoder (a paragraph parser against a line
state machine, a JSON walk against a text parse) and always read BOTH the original format and the encoded format, so a bug in
one side is not mirrored in the other. Conventions used throughout:

* Every encoder is self-gating: it returns the input unchanged unless the result is strictly shorter, and unchanged whenever
  the input does not parse the way the rule needs (so truncated or mutated inputs are left alone rather than half-rewritten).
* Where the encoded form uses an in-band marker (a group header, a stub line) the encoder refuses (returns the input) if the
  input already contains something that looks like the marker; those inputs are `shrinks=None` fixtures.
* Real captures (jstack, go, gdb, git diff, cProfile, jmap, node --cpu-prof, lsof, pyarrow, qrcode, pyfiglet) were taken in
  this container and are inlined; the long ones are shortened only where a comment says so. Formats of tools that are not
  installed here are reconstructed from memory and marked `# reconstructed from memory`.
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import html
import itertools
import json
import math
import random
import re
from collections import Counter, defaultdict

from harness import Fixture, case

# real: jstack <pid> of a JDK 21 process (8 exec threads, 5 task threads, 2 scheduler threads, a lock-order deadlock)
JSTACK = """\
2026-11-04 17:38:23
Full thread dump OpenJDK 64-Bit Server VM (21.0.12.1+1-1-24.04.4-Ubuntu mixed mode, sharing):

Threads class SMR info:
_java_thread_list=0x00007f101c002110, length=28, elements={
0x00007f10a401a7e0, 0x00007f10a412e1f0, 0x00007f10a412f770, 0x00007f10a4130f20,
0x00007f10a4132570, 0x00007f10a4133b20, 0x00007f10a4135670, 0x00007f10a4136d40,
0x00007f10a416ad20, 0x00007f10a416dca0, 0x00007f10a4189400, 0x00007f10a418a0f0,
0x00007f10a418b2f0, 0x00007f10a418c490, 0x00007f10a418d630, 0x00007f10a418e7c0,
0x00007f10a418f9e0, 0x00007f10a4191280, 0x00007f10a4192c70, 0x00007f10a41941d0,
0x00007f10a4195800, 0x00007f10a4196a50, 0x00007f10a4198090, 0x00007f10a419b7a0,
0x00007f10a419c8f0, 0x00007f10a419dc50, 0x00007f10a419ed20, 0x00007f101c000fe0
}

"main" #1 [9735] prio=5 os_prio=0 cpu=57.80ms elapsed=2.29s tid=0x00007f10a401a7e0 nid=9735 waiting on condition  [0x00007f10ab9fe000]
   java.lang.Thread.State: TIMED_WAITING (sleeping)
\tat java.lang.Thread.sleep0(java.base@21.0.12.1/Native Method)
\tat java.lang.Thread.sleep(java.base@21.0.12.1/Thread.java:509)
\tat App.main(App.java:21)

"Reference Handler" #9 [9743] daemon prio=10 os_prio=0 cpu=0.35ms elapsed=2.27s tid=0x00007f10a412e1f0 nid=9743 waiting on condition  [0x00007f1088c88000]
   java.lang.Thread.State: RUNNABLE
\tat java.lang.ref.Reference.waitForReferencePendingList(java.base@21.0.12.1/Native Method)
\tat java.lang.ref.Reference.processPendingReferences(java.base@21.0.12.1/Reference.java:246)
\tat java.lang.ref.Reference$ReferenceHandler.run(java.base@21.0.12.1/Reference.java:208)

"Finalizer" #10 [9744] daemon prio=8 os_prio=0 cpu=0.29ms elapsed=2.27s tid=0x00007f10a412f770 nid=9744 in Object.wait()  [0x00007f1088b88000]
   java.lang.Thread.State: WAITING (on object monitor)
\tat java.lang.Object.wait0(java.base@21.0.12.1/Native Method)
\t- waiting on <0x00000007378025f0> (a java.lang.ref.NativeReferenceQueue$Lock)
\tat java.lang.Object.wait(java.base@21.0.12.1/Object.java:366)
\tat java.lang.Object.wait(java.base@21.0.12.1/Object.java:339)
\tat java.lang.ref.NativeReferenceQueue.await(java.base@21.0.12.1/NativeReferenceQueue.java:48)
\tat java.lang.ref.ReferenceQueue.remove0(java.base@21.0.12.1/ReferenceQueue.java:158)
\tat java.lang.ref.NativeReferenceQueue.remove(java.base@21.0.12.1/NativeReferenceQueue.java:89)
\t- locked <0x00000007378025f0> (a java.lang.ref.NativeReferenceQueue$Lock)
\tat java.lang.ref.Finalizer$FinalizerThread.run(java.base@21.0.12.1/Finalizer.java:173)

"Signal Dispatcher" #11 [9745] daemon prio=9 os_prio=0 cpu=0.48ms elapsed=2.27s tid=0x00007f10a4130f20 nid=9745 waiting on condition  [0x0000000000000000]
   java.lang.Thread.State: RUNNABLE

"Service Thread" #12 [9746] daemon prio=9 os_prio=0 cpu=0.11ms elapsed=2.27s tid=0x00007f10a4132570 nid=9746 runnable  [0x0000000000000000]
   java.lang.Thread.State: RUNNABLE

"Monitor Deflation Thread" #13 [9747] daemon prio=9 os_prio=0 cpu=0.54ms elapsed=2.27s tid=0x00007f10a4133b20 nid=9747 runnable  [0x0000000000000000]
   java.lang.Thread.State: RUNNABLE

"C2 CompilerThread0" #14 [9748] daemon prio=9 os_prio=0 cpu=8.16ms elapsed=2.27s tid=0x00007f10a4135670 nid=9748 waiting on condition  [0x0000000000000000]
   java.lang.Thread.State: RUNNABLE
   No compile task

"C1 CompilerThread0" #16 [9749] daemon prio=9 os_prio=0 cpu=18.45ms elapsed=2.27s tid=0x00007f10a4136d40 nid=9749 waiting on condition  [0x0000000000000000]
   java.lang.Thread.State: RUNNABLE
   No compile task

"Notification Thread" #17 [9750] daemon prio=9 os_prio=0 cpu=0.18ms elapsed=2.27s tid=0x00007f10a416ad20 nid=9750 runnable  [0x0000000000000000]
   java.lang.Thread.State: RUNNABLE

"Common-Cleaner" #18 [9751] daemon prio=8 os_prio=0 cpu=0.48ms elapsed=2.26s tid=0x00007f10a416dca0 nid=9751 waiting on condition  [0x00007f10883b7000]
   java.lang.Thread.State: TIMED_WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x00000007378176b8> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.parkNanos(java.base@21.0.12.1/LockSupport.java:269)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1886)
\tat java.lang.ref.ReferenceQueue.await(java.base@21.0.12.1/ReferenceQueue.java:71)
\tat java.lang.ref.ReferenceQueue.remove0(java.base@21.0.12.1/ReferenceQueue.java:143)
\tat java.lang.ref.ReferenceQueue.remove(java.base@21.0.12.1/ReferenceQueue.java:218)
\tat jdk.internal.ref.CleanerImpl.run(java.base@21.0.12.1/CleanerImpl.java:140)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)
\tat jdk.internal.misc.InnocuousThread.run(java.base@21.0.12.1/InnocuousThread.java:186)

"http-nio-8080-exec-1" #19 [9752] daemon prio=5 os_prio=0 cpu=1.22ms elapsed=2.24s tid=0x00007f10a4189400 nid=9752 waiting on condition  [0x00007f10882b7000]
   java.lang.Thread.State: WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x0000000737827598> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.park(java.base@21.0.12.1/LockSupport.java:371)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionNode.block(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:519)
\tat java.util.concurrent.ForkJoinPool.unmanagedBlock(java.base@21.0.12.1/ForkJoinPool.java:3780)
\tat java.util.concurrent.ForkJoinPool.managedBlock(java.base@21.0.12.1/ForkJoinPool.java:3725)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1746)
\tat java.util.concurrent.LinkedBlockingQueue.take(java.base@21.0.12.1/LinkedBlockingQueue.java:435)
\tat java.util.concurrent.ThreadPoolExecutor.getTask(java.base@21.0.12.1/ThreadPoolExecutor.java:1070)
\tat java.util.concurrent.ThreadPoolExecutor.runWorker(java.base@21.0.12.1/ThreadPoolExecutor.java:1130)
\tat java.util.concurrent.ThreadPoolExecutor$Worker.run(java.base@21.0.12.1/ThreadPoolExecutor.java:642)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"http-nio-8080-exec-2" #20 [9753] daemon prio=5 os_prio=0 cpu=0.90ms elapsed=2.24s tid=0x00007f10a418a0f0 nid=9753 waiting on condition  [0x00007f10881b7000]
   java.lang.Thread.State: WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x0000000737827598> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.park(java.base@21.0.12.1/LockSupport.java:371)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionNode.block(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:519)
\tat java.util.concurrent.ForkJoinPool.unmanagedBlock(java.base@21.0.12.1/ForkJoinPool.java:3780)
\tat java.util.concurrent.ForkJoinPool.managedBlock(java.base@21.0.12.1/ForkJoinPool.java:3725)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1746)
\tat java.util.concurrent.LinkedBlockingQueue.take(java.base@21.0.12.1/LinkedBlockingQueue.java:435)
\tat java.util.concurrent.ThreadPoolExecutor.getTask(java.base@21.0.12.1/ThreadPoolExecutor.java:1070)
\tat java.util.concurrent.ThreadPoolExecutor.runWorker(java.base@21.0.12.1/ThreadPoolExecutor.java:1130)
\tat java.util.concurrent.ThreadPoolExecutor$Worker.run(java.base@21.0.12.1/ThreadPoolExecutor.java:642)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"http-nio-8080-exec-3" #21 [9754] daemon prio=5 os_prio=0 cpu=0.57ms elapsed=2.24s tid=0x00007f10a418b2f0 nid=9754 waiting on condition  [0x00007f1032ffe000]
   java.lang.Thread.State: WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x0000000737827598> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.park(java.base@21.0.12.1/LockSupport.java:371)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionNode.block(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:519)
\tat java.util.concurrent.ForkJoinPool.unmanagedBlock(java.base@21.0.12.1/ForkJoinPool.java:3780)
\tat java.util.concurrent.ForkJoinPool.managedBlock(java.base@21.0.12.1/ForkJoinPool.java:3725)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1746)
\tat java.util.concurrent.LinkedBlockingQueue.take(java.base@21.0.12.1/LinkedBlockingQueue.java:435)
\tat java.util.concurrent.ThreadPoolExecutor.getTask(java.base@21.0.12.1/ThreadPoolExecutor.java:1070)
\tat java.util.concurrent.ThreadPoolExecutor.runWorker(java.base@21.0.12.1/ThreadPoolExecutor.java:1130)
\tat java.util.concurrent.ThreadPoolExecutor$Worker.run(java.base@21.0.12.1/ThreadPoolExecutor.java:642)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"http-nio-8080-exec-4" #22 [9755] daemon prio=5 os_prio=0 cpu=0.46ms elapsed=2.24s tid=0x00007f10a418c490 nid=9755 waiting on condition  [0x00007f1032efe000]
   java.lang.Thread.State: WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x0000000737827598> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.park(java.base@21.0.12.1/LockSupport.java:371)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionNode.block(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:519)
\tat java.util.concurrent.ForkJoinPool.unmanagedBlock(java.base@21.0.12.1/ForkJoinPool.java:3780)
\tat java.util.concurrent.ForkJoinPool.managedBlock(java.base@21.0.12.1/ForkJoinPool.java:3725)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1746)
\tat java.util.concurrent.LinkedBlockingQueue.take(java.base@21.0.12.1/LinkedBlockingQueue.java:435)
\tat java.util.concurrent.ThreadPoolExecutor.getTask(java.base@21.0.12.1/ThreadPoolExecutor.java:1070)
\tat java.util.concurrent.ThreadPoolExecutor.runWorker(java.base@21.0.12.1/ThreadPoolExecutor.java:1130)
\tat java.util.concurrent.ThreadPoolExecutor$Worker.run(java.base@21.0.12.1/ThreadPoolExecutor.java:642)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"http-nio-8080-exec-5" #23 [9756] daemon prio=5 os_prio=0 cpu=0.56ms elapsed=2.24s tid=0x00007f10a418d630 nid=9756 waiting on condition  [0x00007f1032dfe000]
   java.lang.Thread.State: WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x0000000737827598> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.park(java.base@21.0.12.1/LockSupport.java:371)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionNode.block(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:519)
\tat java.util.concurrent.ForkJoinPool.unmanagedBlock(java.base@21.0.12.1/ForkJoinPool.java:3780)
\tat java.util.concurrent.ForkJoinPool.managedBlock(java.base@21.0.12.1/ForkJoinPool.java:3725)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1746)
\tat java.util.concurrent.LinkedBlockingQueue.take(java.base@21.0.12.1/LinkedBlockingQueue.java:435)
\tat java.util.concurrent.ThreadPoolExecutor.getTask(java.base@21.0.12.1/ThreadPoolExecutor.java:1070)
\tat java.util.concurrent.ThreadPoolExecutor.runWorker(java.base@21.0.12.1/ThreadPoolExecutor.java:1130)
\tat java.util.concurrent.ThreadPoolExecutor$Worker.run(java.base@21.0.12.1/ThreadPoolExecutor.java:642)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"http-nio-8080-exec-6" #24 [9757] daemon prio=5 os_prio=0 cpu=0.22ms elapsed=2.24s tid=0x00007f10a418e7c0 nid=9757 waiting on condition  [0x00007f1032cfe000]
   java.lang.Thread.State: WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x0000000737827598> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.park(java.base@21.0.12.1/LockSupport.java:371)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionNode.block(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:519)
\tat java.util.concurrent.ForkJoinPool.unmanagedBlock(java.base@21.0.12.1/ForkJoinPool.java:3780)
\tat java.util.concurrent.ForkJoinPool.managedBlock(java.base@21.0.12.1/ForkJoinPool.java:3725)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1746)
\tat java.util.concurrent.LinkedBlockingQueue.take(java.base@21.0.12.1/LinkedBlockingQueue.java:435)
\tat java.util.concurrent.ThreadPoolExecutor.getTask(java.base@21.0.12.1/ThreadPoolExecutor.java:1070)
\tat java.util.concurrent.ThreadPoolExecutor.runWorker(java.base@21.0.12.1/ThreadPoolExecutor.java:1130)
\tat java.util.concurrent.ThreadPoolExecutor$Worker.run(java.base@21.0.12.1/ThreadPoolExecutor.java:642)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"http-nio-8080-exec-7" #25 [9758] daemon prio=5 os_prio=0 cpu=0.26ms elapsed=2.24s tid=0x00007f10a418f9e0 nid=9758 waiting on condition  [0x00007f1032bfe000]
   java.lang.Thread.State: WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x0000000737827598> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.park(java.base@21.0.12.1/LockSupport.java:371)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionNode.block(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:519)
\tat java.util.concurrent.ForkJoinPool.unmanagedBlock(java.base@21.0.12.1/ForkJoinPool.java:3780)
\tat java.util.concurrent.ForkJoinPool.managedBlock(java.base@21.0.12.1/ForkJoinPool.java:3725)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1746)
\tat java.util.concurrent.LinkedBlockingQueue.take(java.base@21.0.12.1/LinkedBlockingQueue.java:435)
\tat java.util.concurrent.ThreadPoolExecutor.getTask(java.base@21.0.12.1/ThreadPoolExecutor.java:1070)
\tat java.util.concurrent.ThreadPoolExecutor.runWorker(java.base@21.0.12.1/ThreadPoolExecutor.java:1130)
\tat java.util.concurrent.ThreadPoolExecutor$Worker.run(java.base@21.0.12.1/ThreadPoolExecutor.java:642)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"http-nio-8080-exec-8" #26 [9759] daemon prio=5 os_prio=0 cpu=0.13ms elapsed=2.24s tid=0x00007f10a4191280 nid=9759 waiting on condition  [0x00007f1032afe000]
   java.lang.Thread.State: WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x0000000737827598> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.park(java.base@21.0.12.1/LockSupport.java:371)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionNode.block(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:519)
\tat java.util.concurrent.ForkJoinPool.unmanagedBlock(java.base@21.0.12.1/ForkJoinPool.java:3780)
\tat java.util.concurrent.ForkJoinPool.managedBlock(java.base@21.0.12.1/ForkJoinPool.java:3725)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1746)
\tat java.util.concurrent.LinkedBlockingQueue.take(java.base@21.0.12.1/LinkedBlockingQueue.java:435)
\tat java.util.concurrent.ThreadPoolExecutor.getTask(java.base@21.0.12.1/ThreadPoolExecutor.java:1070)
\tat java.util.concurrent.ThreadPoolExecutor.runWorker(java.base@21.0.12.1/ThreadPoolExecutor.java:1130)
\tat java.util.concurrent.ThreadPoolExecutor$Worker.run(java.base@21.0.12.1/ThreadPoolExecutor.java:642)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"task-1" #27 [9760] daemon prio=5 os_prio=0 cpu=0.24ms elapsed=2.24s tid=0x00007f10a4192c70 nid=9760 waiting on condition  [0x00007f10329fe000]
   java.lang.Thread.State: WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x000000073792ebc8> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.park(java.base@21.0.12.1/LockSupport.java:371)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionNode.block(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:519)
\tat java.util.concurrent.ForkJoinPool.unmanagedBlock(java.base@21.0.12.1/ForkJoinPool.java:3780)
\tat java.util.concurrent.ForkJoinPool.managedBlock(java.base@21.0.12.1/ForkJoinPool.java:3725)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1746)
\tat java.util.concurrent.LinkedBlockingQueue.take(java.base@21.0.12.1/LinkedBlockingQueue.java:435)
\tat java.util.concurrent.ThreadPoolExecutor.getTask(java.base@21.0.12.1/ThreadPoolExecutor.java:1070)
\tat java.util.concurrent.ThreadPoolExecutor.runWorker(java.base@21.0.12.1/ThreadPoolExecutor.java:1130)
\tat java.util.concurrent.ThreadPoolExecutor$Worker.run(java.base@21.0.12.1/ThreadPoolExecutor.java:642)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"task-2" #28 [9761] daemon prio=5 os_prio=0 cpu=0.18ms elapsed=2.24s tid=0x00007f10a41941d0 nid=9761 waiting on condition  [0x00007f10328fe000]
   java.lang.Thread.State: WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x000000073792ebc8> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.park(java.base@21.0.12.1/LockSupport.java:371)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionNode.block(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:519)
\tat java.util.concurrent.ForkJoinPool.unmanagedBlock(java.base@21.0.12.1/ForkJoinPool.java:3780)
\tat java.util.concurrent.ForkJoinPool.managedBlock(java.base@21.0.12.1/ForkJoinPool.java:3725)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1746)
\tat java.util.concurrent.LinkedBlockingQueue.take(java.base@21.0.12.1/LinkedBlockingQueue.java:435)
\tat java.util.concurrent.ThreadPoolExecutor.getTask(java.base@21.0.12.1/ThreadPoolExecutor.java:1070)
\tat java.util.concurrent.ThreadPoolExecutor.runWorker(java.base@21.0.12.1/ThreadPoolExecutor.java:1130)
\tat java.util.concurrent.ThreadPoolExecutor$Worker.run(java.base@21.0.12.1/ThreadPoolExecutor.java:642)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"task-3" #29 [9762] daemon prio=5 os_prio=0 cpu=0.17ms elapsed=2.24s tid=0x00007f10a4195800 nid=9762 waiting on condition  [0x00007f10327fe000]
   java.lang.Thread.State: WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x000000073792ebc8> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.park(java.base@21.0.12.1/LockSupport.java:371)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionNode.block(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:519)
\tat java.util.concurrent.ForkJoinPool.unmanagedBlock(java.base@21.0.12.1/ForkJoinPool.java:3780)
\tat java.util.concurrent.ForkJoinPool.managedBlock(java.base@21.0.12.1/ForkJoinPool.java:3725)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1746)
\tat java.util.concurrent.LinkedBlockingQueue.take(java.base@21.0.12.1/LinkedBlockingQueue.java:435)
\tat java.util.concurrent.ThreadPoolExecutor.getTask(java.base@21.0.12.1/ThreadPoolExecutor.java:1070)
\tat java.util.concurrent.ThreadPoolExecutor.runWorker(java.base@21.0.12.1/ThreadPoolExecutor.java:1130)
\tat java.util.concurrent.ThreadPoolExecutor$Worker.run(java.base@21.0.12.1/ThreadPoolExecutor.java:642)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"task-4" #30 [9763] daemon prio=5 os_prio=0 cpu=0.27ms elapsed=2.24s tid=0x00007f10a4196a50 nid=9763 waiting on condition  [0x00007f10326fe000]
   java.lang.Thread.State: WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x000000073792ebc8> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.park(java.base@21.0.12.1/LockSupport.java:371)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionNode.block(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:519)
\tat java.util.concurrent.ForkJoinPool.unmanagedBlock(java.base@21.0.12.1/ForkJoinPool.java:3780)
\tat java.util.concurrent.ForkJoinPool.managedBlock(java.base@21.0.12.1/ForkJoinPool.java:3725)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1746)
\tat java.util.concurrent.LinkedBlockingQueue.take(java.base@21.0.12.1/LinkedBlockingQueue.java:435)
\tat java.util.concurrent.ThreadPoolExecutor.getTask(java.base@21.0.12.1/ThreadPoolExecutor.java:1070)
\tat java.util.concurrent.ThreadPoolExecutor.runWorker(java.base@21.0.12.1/ThreadPoolExecutor.java:1130)
\tat java.util.concurrent.ThreadPoolExecutor$Worker.run(java.base@21.0.12.1/ThreadPoolExecutor.java:642)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"task-5" #31 [9764] daemon prio=5 os_prio=0 cpu=0.21ms elapsed=2.24s tid=0x00007f10a4198090 nid=9764 waiting on condition  [0x00007f10325fe000]
   java.lang.Thread.State: WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x000000073792ebc8> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.park(java.base@21.0.12.1/LockSupport.java:371)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionNode.block(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:519)
\tat java.util.concurrent.ForkJoinPool.unmanagedBlock(java.base@21.0.12.1/ForkJoinPool.java:3780)
\tat java.util.concurrent.ForkJoinPool.managedBlock(java.base@21.0.12.1/ForkJoinPool.java:3725)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1746)
\tat java.util.concurrent.LinkedBlockingQueue.take(java.base@21.0.12.1/LinkedBlockingQueue.java:435)
\tat java.util.concurrent.ThreadPoolExecutor.getTask(java.base@21.0.12.1/ThreadPoolExecutor.java:1070)
\tat java.util.concurrent.ThreadPoolExecutor.runWorker(java.base@21.0.12.1/ThreadPoolExecutor.java:1130)
\tat java.util.concurrent.ThreadPoolExecutor$Worker.run(java.base@21.0.12.1/ThreadPoolExecutor.java:642)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"scheduling-1" #32 [9765] daemon prio=5 os_prio=0 cpu=0.28ms elapsed=2.23s tid=0x00007f10a419b7a0 nid=9765 waiting on condition  [0x00007f10324fe000]
   java.lang.Thread.State: TIMED_WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x00000007379315b0> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.parkNanos(java.base@21.0.12.1/LockSupport.java:269)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.awaitNanos(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1797)
\tat java.util.concurrent.ScheduledThreadPoolExecutor$DelayedWorkQueue.take(java.base@21.0.12.1/ScheduledThreadPoolExecutor.java:1182)
\tat java.util.concurrent.ScheduledThreadPoolExecutor$DelayedWorkQueue.take(java.base@21.0.12.1/ScheduledThreadPoolExecutor.java:899)
\tat java.util.concurrent.ThreadPoolExecutor.getTask(java.base@21.0.12.1/ThreadPoolExecutor.java:1070)
\tat java.util.concurrent.ThreadPoolExecutor.runWorker(java.base@21.0.12.1/ThreadPoolExecutor.java:1130)
\tat java.util.concurrent.ThreadPoolExecutor$Worker.run(java.base@21.0.12.1/ThreadPoolExecutor.java:642)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"scheduling-2" #33 [9766] daemon prio=5 os_prio=0 cpu=0.62ms elapsed=2.23s tid=0x00007f10a419c8f0 nid=9766 waiting on condition  [0x00007f10323fe000]
   java.lang.Thread.State: WAITING (parking)
\tat jdk.internal.misc.Unsafe.park(java.base@21.0.12.1/Native Method)
\t- parking to wait for  <0x00000007379315b0> (a java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject)
\tat java.util.concurrent.locks.LockSupport.park(java.base@21.0.12.1/LockSupport.java:371)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionNode.block(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:519)
\tat java.util.concurrent.ForkJoinPool.unmanagedBlock(java.base@21.0.12.1/ForkJoinPool.java:3780)
\tat java.util.concurrent.ForkJoinPool.managedBlock(java.base@21.0.12.1/ForkJoinPool.java:3725)
\tat java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject.await(java.base@21.0.12.1/AbstractQueuedSynchronizer.java:1746)
\tat java.util.concurrent.ScheduledThreadPoolExecutor$DelayedWorkQueue.take(java.base@21.0.12.1/ScheduledThreadPoolExecutor.java:1177)
\tat java.util.concurrent.ScheduledThreadPoolExecutor$DelayedWorkQueue.take(java.base@21.0.12.1/ScheduledThreadPoolExecutor.java:899)
\tat java.util.concurrent.ThreadPoolExecutor.getTask(java.base@21.0.12.1/ThreadPoolExecutor.java:1070)
\tat java.util.concurrent.ThreadPoolExecutor.runWorker(java.base@21.0.12.1/ThreadPoolExecutor.java:1130)
\tat java.util.concurrent.ThreadPoolExecutor$Worker.run(java.base@21.0.12.1/ThreadPoolExecutor.java:642)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"order-worker-1" #34 [9767] daemon prio=5 os_prio=0 cpu=0.55ms elapsed=2.23s tid=0x00007f10a419dc50 nid=9767 waiting for monitor entry  [0x00007f10322fe000]
   java.lang.Thread.State: BLOCKED (on object monitor)
\tat App.lambda$main$5(App.java:18)
\t- waiting to lock <0x000000073781a868> (a java.lang.Object)
\t- locked <0x000000073781a858> (a java.lang.Object)
\tat App$$Lambda/0x00007f1034005000.run(Unknown Source)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"order-worker-2" #35 [9768] daemon prio=5 os_prio=0 cpu=0.72ms elapsed=2.23s tid=0x00007f10a419ed20 nid=9768 waiting for monitor entry  [0x00007f10321fe000]
   java.lang.Thread.State: BLOCKED (on object monitor)
\tat App.lambda$main$6(App.java:19)
\t- waiting to lock <0x000000073781a858> (a java.lang.Object)
\t- locked <0x000000073781a868> (a java.lang.Object)
\tat App$$Lambda/0x00007f1034005220.run(Unknown Source)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

"Attach Listener" #36 [9803] daemon prio=9 os_prio=0 cpu=0.32ms elapsed=0.10s tid=0x00007f101c000fe0 nid=9803 waiting on condition  [0x0000000000000000]
   java.lang.Thread.State: RUNNABLE

"VM Thread" os_prio=0 cpu=0.86ms elapsed=2.27s tid=0x00007f10a41258d0 nid=9742 runnable  

"VM Periodic Task Thread" os_prio=0 cpu=2.52ms elapsed=2.28s tid=0x00007f10a4114030 nid=9741 waiting on condition  

"G1 Service" os_prio=0 cpu=0.20ms elapsed=2.29s tid=0x00007f10a40fa9d0 nid=9740 runnable  

"G1 Refine#0" os_prio=0 cpu=0.15ms elapsed=2.29s tid=0x00007f10a40f9a00 nid=9739 runnable  

"G1 Conc#0" os_prio=0 cpu=0.07ms elapsed=2.29s tid=0x00007f10a4086eb0 nid=9738 runnable  

"G1 Main Marker" os_prio=0 cpu=0.16ms elapsed=2.29s tid=0x00007f10a4085f00 nid=9737 runnable  

"GC Thread#0" os_prio=0 cpu=0.17ms elapsed=2.29s tid=0x00007f10a4077400 nid=9736 runnable  

JNI global refs: 5, weak refs: 0


Found one Java-level deadlock:
=============================
"order-worker-1":
  waiting to lock monitor 0x00007f0fcc0016e0 (object 0x000000073781a868, a java.lang.Object),
  which is held by "order-worker-2"

"order-worker-2":
  waiting to lock monitor 0x00007f0fc8001770 (object 0x000000073781a858, a java.lang.Object),
  which is held by "order-worker-1"

Java stack information for the threads listed above:
===================================================
"order-worker-1":
\tat App.lambda$main$5(App.java:18)
\t- waiting to lock <0x000000073781a868> (a java.lang.Object)
\t- locked <0x000000073781a858> (a java.lang.Object)
\tat App$$Lambda/0x00007f1034005000.run(Unknown Source)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)
"order-worker-2":
\tat App.lambda$main$6(App.java:19)
\t- waiting to lock <0x000000073781a858> (a java.lang.Object)
\t- locked <0x000000073781a868> (a java.lang.Object)
\tat App$$Lambda/0x00007f1034005220.run(Unknown Source)
\tat java.lang.Thread.runWith(java.base@21.0.12.1/Thread.java:1596)
\tat java.lang.Thread.run(java.base@21.0.12.1/Thread.java:1583)

Found 1 deadlock.

"""

# real: go1.24 'fatal error: all goroutines are asleep - deadlock!' dump (16 idle workers, 3 mutex waiters, 1 holder); source path shortened
GOSTACK = """\
fatal error: all goroutines are asleep - deadlock!

goroutine 1 [sync.WaitGroup.Wait]:
sync.runtime_SemacquireWaitGroup(0xc00000e0c0?)
\t/usr/local/go1.24.7/src/runtime/sema.go:110 +0x25
sync.(*WaitGroup).Wait(0x0?)
\t/usr/local/go1.24.7/src/sync/waitgroup.go:118 +0x48
main.main()
\t/srv/orders/main.go:47 +0x1d1

goroutine 6 [chan receive]:
main.worker(0x0, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 7 [chan receive]:
main.worker(0x1, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 8 [chan receive]:
main.worker(0x2, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 9 [chan receive]:
main.worker(0x3, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 10 [chan receive]:
main.worker(0x4, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 11 [chan receive]:
main.worker(0x5, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 12 [chan receive]:
main.worker(0x6, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 13 [chan receive]:
main.worker(0x7, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 14 [chan receive]:
main.worker(0x8, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 15 [chan receive]:
main.worker(0x9, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 16 [chan receive]:
main.worker(0xa, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 17 [chan receive]:
main.worker(0xb, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 18 [chan receive]:
main.worker(0xc, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 19 [chan receive]:
main.worker(0xd, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 20 [chan receive]:
main.worker(0xe, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 21 [chan receive]:
main.worker(0xf, 0xc000026150, 0x0?)
\t/srv/orders/main.go:27 +0x105
created by main.main in goroutine 1
\t/srv/orders/main.go:37 +0x4c

goroutine 22 [sync.Mutex.Lock]:
internal/sync.runtime_SemacquireMutex(0x0?, 0xf0?, 0xc00011a6f0?)
\t/usr/local/go1.24.7/src/runtime/sema.go:95 +0x25
internal/sync.(*Mutex).lockSlow(0xc000012090)
\t/usr/local/go1.24.7/src/internal/sync/mutex.go:149 +0x15d
internal/sync.(*Mutex).Lock(...)
\t/usr/local/go1.24.7/src/internal/sync/mutex.go:70
sync.(*Mutex).Lock(...)
\t/usr/local/go1.24.7/src/sync/mutex.go:46
main.(*Store).Reserve(0xc000012090, {0xc000010110, 0x5})
\t/srv/orders/main.go:14 +0x57
main.main.func1(0x0?)
\t/srv/orders/main.go:44 +0x95
created by main.main in goroutine 1
\t/srv/orders/main.go:42 +0x111

goroutine 23 [sync.Mutex.Lock]:
internal/sync.runtime_SemacquireMutex(0x0?, 0xf0?, 0xc00011aef0?)
\t/usr/local/go1.24.7/src/runtime/sema.go:95 +0x25
internal/sync.(*Mutex).lockSlow(0xc000012090)
\t/usr/local/go1.24.7/src/internal/sync/mutex.go:149 +0x15d
internal/sync.(*Mutex).Lock(...)
\t/usr/local/go1.24.7/src/internal/sync/mutex.go:70
sync.(*Mutex).Lock(...)
\t/usr/local/go1.24.7/src/sync/mutex.go:46
main.(*Store).Reserve(0xc000012090, {0xc000010115, 0x5})
\t/srv/orders/main.go:14 +0x57
main.main.func1(0x0?)
\t/srv/orders/main.go:44 +0x95
created by main.main in goroutine 1
\t/srv/orders/main.go:42 +0x111

goroutine 24 [sync.Mutex.Lock]:
internal/sync.runtime_SemacquireMutex(0x0?, 0xf0?, 0xc00011b6f0?)
\t/usr/local/go1.24.7/src/runtime/sema.go:95 +0x25
internal/sync.(*Mutex).lockSlow(0xc000012090)
\t/usr/local/go1.24.7/src/internal/sync/mutex.go:149 +0x15d
internal/sync.(*Mutex).Lock(...)
\t/usr/local/go1.24.7/src/internal/sync/mutex.go:70
sync.(*Mutex).Lock(...)
\t/usr/local/go1.24.7/src/sync/mutex.go:46
main.(*Store).Reserve(0xc000012090, {0xc00001011a, 0x5})
\t/srv/orders/main.go:14 +0x57
main.main.func1(0x0?)
\t/srv/orders/main.go:44 +0x95
created by main.main in goroutine 1
\t/srv/orders/main.go:42 +0x111

goroutine 25 [sync.Mutex.Lock]:
internal/sync.runtime_SemacquireMutex(0xc00011bec0?, 0x92?, 0xded3d7ba535a4011?)
\t/usr/local/go1.24.7/src/runtime/sema.go:95 +0x25
internal/sync.(*Mutex).lockSlow(0xc000012090)
\t/usr/local/go1.24.7/src/internal/sync/mutex.go:149 +0x15d
internal/sync.(*Mutex).Lock(...)
\t/usr/local/go1.24.7/src/internal/sync/mutex.go:70
sync.(*Mutex).Lock(...)
\t/usr/local/go1.24.7/src/sync/mutex.go:46
main.(*Store).flush(0x4a3780?)
\t/srv/orders/main.go:21 +0x31
main.(*Store).Reserve(0xc000012090, {0xc000010108?, 0xc00011bfa0?})
\t/srv/orders/main.go:17 +0xac
main.main.func1(0x0?)
\t/srv/orders/main.go:44 +0x95
created by main.main in goroutine 1
\t/srv/orders/main.go:42 +0x111
"""

# real: gdb -p PID -batch -ex 'thread apply all bt' on a pthread pool (8 cond-wait workers, 3 mutex waiters, main)
GDBSTACK = """\
[New LWP 9828]
[New LWP 9827]
[New LWP 9826]
[New LWP 9825]
[New LWP 9824]
[New LWP 9823]
[New LWP 9822]
[New LWP 9821]
[New LWP 9820]
[New LWP 9819]
[New LWP 9818]
[Thread debugging using libthread_db enabled]
Using host libthread_db library "/lib/x86_64-linux-gnu/libthread_db.so.1".
0x00007f86434ecbdf in __GI___clock_nanosleep (clock_id=clock_id@entry=0, flags=flags@entry=0, req=req@entry=0x7ffcef16d640, rem=rem@entry=0x7ffcef16d640) at ../sysdeps/unix/sysv/linux/clock_nanosleep.c:78

Thread 12 (Thread 0x7f86433ff6c0 (LWP 9818) "worker-0"):
#0  0x00007f8643498e51 in __futex_abstimed_wait_common64 (private=0, cancel=true, abstime=0x0, op=393, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:57
#1  __futex_abstimed_wait_common (cancel=true, private=0, abstime=0x0, clockid=0, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:87
#2  __GI___futex_abstimed_wait_cancelable64 (futex_word=futex_word@entry=0x558520faa0a8 <qcv+40>, expected=expected@entry=0, clockid=clockid@entry=0, abstime=abstime@entry=0x0, private=private@entry=0) at ./nptl/futex-internal.c:139
#3  0x00007f864349b8cd in __pthread_cond_wait_common (abstime=0x0, clockid=0, mutex=0x558520faa040 <qmu>, cond=0x558520faa080 <qcv>) at ./nptl/pthread_cond_wait.c:503
#4  ___pthread_cond_wait (cond=0x558520faa080 <qcv>, mutex=0x558520faa040 <qmu>) at ./nptl/pthread_cond_wait.c:627
#5  0x0000558520fa727b in wait_for_job () at pool.c:13
#6  0x0000558520fa730a in worker (arg=0x0) at pool.c:22
#7  0x00007f864349cb84 in start_thread (arg=<optimized out>) at ./nptl/pthread_create.c:447
#8  0x00007f8643529ecc in clone3 () at ../sysdeps/unix/sysv/linux/x86_64/clone3.S:78

Thread 11 (Thread 0x7f8642bfe6c0 (LWP 9819) "worker-1"):
#0  0x00007f8643498e51 in __futex_abstimed_wait_common64 (private=0, cancel=true, abstime=0x0, op=393, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:57
#1  __futex_abstimed_wait_common (cancel=true, private=0, abstime=0x0, clockid=0, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:87
#2  __GI___futex_abstimed_wait_cancelable64 (futex_word=futex_word@entry=0x558520faa0a8 <qcv+40>, expected=expected@entry=0, clockid=clockid@entry=0, abstime=abstime@entry=0x0, private=private@entry=0) at ./nptl/futex-internal.c:139
#3  0x00007f864349b8cd in __pthread_cond_wait_common (abstime=0x0, clockid=0, mutex=0x558520faa040 <qmu>, cond=0x558520faa080 <qcv>) at ./nptl/pthread_cond_wait.c:503
#4  ___pthread_cond_wait (cond=0x558520faa080 <qcv>, mutex=0x558520faa040 <qmu>) at ./nptl/pthread_cond_wait.c:627
#5  0x0000558520fa727b in wait_for_job () at pool.c:13
#6  0x0000558520fa730a in worker (arg=0x1) at pool.c:22
#7  0x00007f864349cb84 in start_thread (arg=<optimized out>) at ./nptl/pthread_create.c:447
#8  0x00007f8643529ecc in clone3 () at ../sysdeps/unix/sysv/linux/x86_64/clone3.S:78

Thread 10 (Thread 0x7f86423fd6c0 (LWP 9820) "worker-2"):
#0  0x00007f8643498e51 in __futex_abstimed_wait_common64 (private=0, cancel=true, abstime=0x0, op=393, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:57
#1  __futex_abstimed_wait_common (cancel=true, private=0, abstime=0x0, clockid=0, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:87
#2  __GI___futex_abstimed_wait_cancelable64 (futex_word=futex_word@entry=0x558520faa0a8 <qcv+40>, expected=expected@entry=0, clockid=clockid@entry=0, abstime=abstime@entry=0x0, private=private@entry=0) at ./nptl/futex-internal.c:139
#3  0x00007f864349b8cd in __pthread_cond_wait_common (abstime=0x0, clockid=0, mutex=0x558520faa040 <qmu>, cond=0x558520faa080 <qcv>) at ./nptl/pthread_cond_wait.c:503
#4  ___pthread_cond_wait (cond=0x558520faa080 <qcv>, mutex=0x558520faa040 <qmu>) at ./nptl/pthread_cond_wait.c:627
#5  0x0000558520fa727b in wait_for_job () at pool.c:13
#6  0x0000558520fa730a in worker (arg=0x2) at pool.c:22
#7  0x00007f864349cb84 in start_thread (arg=<optimized out>) at ./nptl/pthread_create.c:447
#8  0x00007f8643529ecc in clone3 () at ../sysdeps/unix/sysv/linux/x86_64/clone3.S:78

Thread 9 (Thread 0x7f8641bfc6c0 (LWP 9821) "worker-3"):
#0  0x00007f8643498e51 in __futex_abstimed_wait_common64 (private=0, cancel=true, abstime=0x0, op=393, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:57
#1  __futex_abstimed_wait_common (cancel=true, private=0, abstime=0x0, clockid=0, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:87
#2  __GI___futex_abstimed_wait_cancelable64 (futex_word=futex_word@entry=0x558520faa0a8 <qcv+40>, expected=expected@entry=0, clockid=clockid@entry=0, abstime=abstime@entry=0x0, private=private@entry=0) at ./nptl/futex-internal.c:139
#3  0x00007f864349b8cd in __pthread_cond_wait_common (abstime=0x0, clockid=0, mutex=0x558520faa040 <qmu>, cond=0x558520faa080 <qcv>) at ./nptl/pthread_cond_wait.c:503
#4  ___pthread_cond_wait (cond=0x558520faa080 <qcv>, mutex=0x558520faa040 <qmu>) at ./nptl/pthread_cond_wait.c:627
#5  0x0000558520fa727b in wait_for_job () at pool.c:13
#6  0x0000558520fa730a in worker (arg=0x3) at pool.c:22
#7  0x00007f864349cb84 in start_thread (arg=<optimized out>) at ./nptl/pthread_create.c:447
#8  0x00007f8643529ecc in clone3 () at ../sysdeps/unix/sysv/linux/x86_64/clone3.S:78

Thread 8 (Thread 0x7f86413fb6c0 (LWP 9822) "worker-4"):
#0  0x00007f8643498e51 in __futex_abstimed_wait_common64 (private=0, cancel=true, abstime=0x0, op=393, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:57
#1  __futex_abstimed_wait_common (cancel=true, private=0, abstime=0x0, clockid=0, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:87
#2  __GI___futex_abstimed_wait_cancelable64 (futex_word=futex_word@entry=0x558520faa0a8 <qcv+40>, expected=expected@entry=0, clockid=clockid@entry=0, abstime=abstime@entry=0x0, private=private@entry=0) at ./nptl/futex-internal.c:139
#3  0x00007f864349b8cd in __pthread_cond_wait_common (abstime=0x0, clockid=0, mutex=0x558520faa040 <qmu>, cond=0x558520faa080 <qcv>) at ./nptl/pthread_cond_wait.c:503
#4  ___pthread_cond_wait (cond=0x558520faa080 <qcv>, mutex=0x558520faa040 <qmu>) at ./nptl/pthread_cond_wait.c:627
#5  0x0000558520fa727b in wait_for_job () at pool.c:13
#6  0x0000558520fa730a in worker (arg=0x4) at pool.c:22
#7  0x00007f864349cb84 in start_thread (arg=<optimized out>) at ./nptl/pthread_create.c:447
#8  0x00007f8643529ecc in clone3 () at ../sysdeps/unix/sysv/linux/x86_64/clone3.S:78

Thread 7 (Thread 0x7f8640bfa6c0 (LWP 9823) "worker-5"):
#0  0x00007f8643498e51 in __futex_abstimed_wait_common64 (private=0, cancel=true, abstime=0x0, op=393, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:57
#1  __futex_abstimed_wait_common (cancel=true, private=0, abstime=0x0, clockid=0, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:87
#2  __GI___futex_abstimed_wait_cancelable64 (futex_word=futex_word@entry=0x558520faa0a8 <qcv+40>, expected=expected@entry=0, clockid=clockid@entry=0, abstime=abstime@entry=0x0, private=private@entry=0) at ./nptl/futex-internal.c:139
#3  0x00007f864349b8cd in __pthread_cond_wait_common (abstime=0x0, clockid=0, mutex=0x558520faa040 <qmu>, cond=0x558520faa080 <qcv>) at ./nptl/pthread_cond_wait.c:503
#4  ___pthread_cond_wait (cond=0x558520faa080 <qcv>, mutex=0x558520faa040 <qmu>) at ./nptl/pthread_cond_wait.c:627
#5  0x0000558520fa727b in wait_for_job () at pool.c:13
#6  0x0000558520fa730a in worker (arg=0x5) at pool.c:22
#7  0x00007f864349cb84 in start_thread (arg=<optimized out>) at ./nptl/pthread_create.c:447
#8  0x00007f8643529ecc in clone3 () at ../sysdeps/unix/sysv/linux/x86_64/clone3.S:78

Thread 6 (Thread 0x7f86403f96c0 (LWP 9824) "worker-6"):
#0  0x00007f8643498e51 in __futex_abstimed_wait_common64 (private=0, cancel=true, abstime=0x0, op=393, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:57
#1  __futex_abstimed_wait_common (cancel=true, private=0, abstime=0x0, clockid=0, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:87
#2  __GI___futex_abstimed_wait_cancelable64 (futex_word=futex_word@entry=0x558520faa0a8 <qcv+40>, expected=expected@entry=0, clockid=clockid@entry=0, abstime=abstime@entry=0x0, private=private@entry=0) at ./nptl/futex-internal.c:139
#3  0x00007f864349b8cd in __pthread_cond_wait_common (abstime=0x0, clockid=0, mutex=0x558520faa040 <qmu>, cond=0x558520faa080 <qcv>) at ./nptl/pthread_cond_wait.c:503
#4  ___pthread_cond_wait (cond=0x558520faa080 <qcv>, mutex=0x558520faa040 <qmu>) at ./nptl/pthread_cond_wait.c:627
#5  0x0000558520fa727b in wait_for_job () at pool.c:13
#6  0x0000558520fa730a in worker (arg=0x6) at pool.c:22
#7  0x00007f864349cb84 in start_thread (arg=<optimized out>) at ./nptl/pthread_create.c:447
#8  0x00007f8643529ecc in clone3 () at ../sysdeps/unix/sysv/linux/x86_64/clone3.S:78

Thread 5 (Thread 0x7f863fbf86c0 (LWP 9825) "worker-7"):
#0  0x00007f8643498e51 in __futex_abstimed_wait_common64 (private=0, cancel=true, abstime=0x0, op=393, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:57
#1  __futex_abstimed_wait_common (cancel=true, private=0, abstime=0x0, clockid=0, expected=0, futex_word=0x558520faa0a8 <qcv+40>) at ./nptl/futex-internal.c:87
#2  __GI___futex_abstimed_wait_cancelable64 (futex_word=futex_word@entry=0x558520faa0a8 <qcv+40>, expected=expected@entry=0, clockid=clockid@entry=0, abstime=abstime@entry=0x0, private=private@entry=0) at ./nptl/futex-internal.c:139
#3  0x00007f864349b8cd in __pthread_cond_wait_common (abstime=0x0, clockid=0, mutex=0x558520faa040 <qmu>, cond=0x558520faa080 <qcv>) at ./nptl/pthread_cond_wait.c:503
#4  ___pthread_cond_wait (cond=0x558520faa080 <qcv>, mutex=0x558520faa040 <qmu>) at ./nptl/pthread_cond_wait.c:627
#5  0x0000558520fa727b in wait_for_job () at pool.c:13
#6  0x0000558520fa730a in worker (arg=0x7) at pool.c:22
#7  0x00007f864349cb84 in start_thread (arg=<optimized out>) at ./nptl/pthread_create.c:447
#8  0x00007f8643529ecc in clone3 () at ../sysdeps/unix/sysv/linux/x86_64/clone3.S:78

Thread 4 (Thread 0x7f863f3f76c0 (LWP 9826) "handler-0"):
#0  futex_wait (private=0, expected=2, futex_word=0x558520faa0c0 <big>) at ../sysdeps/nptl/futex-internal.h:146
#1  __GI___lll_lock_wait (futex=futex@entry=0x558520faa0c0 <big>, private=0) at ./nptl/lowlevellock.c:49
#2  0x00007f86434a01e1 in lll_mutex_lock_optimized (mutex=0x558520faa0c0 <big>) at ./nptl/pthread_mutex_lock.c:48
#3  ___pthread_mutex_lock (mutex=0x558520faa0c0 <big>) at ./nptl/pthread_mutex_lock.c:93
#4  0x0000558520fa7323 in critical () at pool.c:26
#5  0x0000558520fa738a in handler (arg=0x0) at pool.c:32
#6  0x00007f864349cb84 in start_thread (arg=<optimized out>) at ./nptl/pthread_create.c:447
#7  0x00007f8643529ecc in clone3 () at ../sysdeps/unix/sysv/linux/x86_64/clone3.S:78

Thread 3 (Thread 0x7f863ebf66c0 (LWP 9827) "handler-1"):
#0  futex_wait (private=0, expected=2, futex_word=0x558520faa0c0 <big>) at ../sysdeps/nptl/futex-internal.h:146
#1  __GI___lll_lock_wait (futex=futex@entry=0x558520faa0c0 <big>, private=0) at ./nptl/lowlevellock.c:49
#2  0x00007f86434a01e1 in lll_mutex_lock_optimized (mutex=0x558520faa0c0 <big>) at ./nptl/pthread_mutex_lock.c:48
#3  ___pthread_mutex_lock (mutex=0x558520faa0c0 <big>) at ./nptl/pthread_mutex_lock.c:93
#4  0x0000558520fa7323 in critical () at pool.c:26
#5  0x0000558520fa738a in handler (arg=0x1) at pool.c:32
#6  0x00007f864349cb84 in start_thread (arg=<optimized out>) at ./nptl/pthread_create.c:447
#7  0x00007f8643529ecc in clone3 () at ../sysdeps/unix/sysv/linux/x86_64/clone3.S:78

Thread 2 (Thread 0x7f863e3f56c0 (LWP 9828) "handler-2"):
#0  futex_wait (private=0, expected=2, futex_word=0x558520faa0c0 <big>) at ../sysdeps/nptl/futex-internal.h:146
#1  __GI___lll_lock_wait (futex=futex@entry=0x558520faa0c0 <big>, private=0) at ./nptl/lowlevellock.c:49
#2  0x00007f86434a01e1 in lll_mutex_lock_optimized (mutex=0x558520faa0c0 <big>) at ./nptl/pthread_mutex_lock.c:48
#3  ___pthread_mutex_lock (mutex=0x558520faa0c0 <big>) at ./nptl/pthread_mutex_lock.c:93
#4  0x0000558520fa7323 in critical () at pool.c:26
#5  0x0000558520fa738a in handler (arg=0x2) at pool.c:32
#6  0x00007f864349cb84 in start_thread (arg=<optimized out>) at ./nptl/pthread_create.c:447
#7  0x00007f8643529ecc in clone3 () at ../sysdeps/unix/sysv/linux/x86_64/clone3.S:78

Thread 1 (Thread 0x7f86436f6740 (LWP 9816) "pool"):
#0  0x00007f86434ecbdf in __GI___clock_nanosleep (clock_id=clock_id@entry=0, flags=flags@entry=0, req=req@entry=0x7ffcef16d640, rem=rem@entry=0x7ffcef16d640) at ../sysdeps/unix/sysv/linux/clock_nanosleep.c:78
#1  0x00007f86434f9b27 in __GI___nanosleep (req=req@entry=0x7ffcef16d640, rem=rem@entry=0x7ffcef16d640) at ../sysdeps/unix/sysv/linux/nanosleep.c:25
#2  0x00007f864350ed93 in __sleep (seconds=0) at ../sysdeps/posix/sleep.c:55
#3  0x0000558520fa7443 in main () at pool.c:41
[Inferior 1 (process 9816) detached]
"""

# real: git diff after sed 's/\bResponse\b/HTTPResponse/' over httpcore2 (17 files) plus two hand edits
RENAME_DIFF = """\
diff --git a/httpcore2/__init__.py b/httpcore2/__init__.py
index 708eea9..5fd47b4 100644
--- a/httpcore2/__init__.py
+++ b/httpcore2/__init__.py
@@ -36,7 +36,7 @@ from ._exceptions import (
     WriteError,
     WriteTimeout,
 )
-from ._models import URL, Origin, Proxy, Request, Response
+from ._models import URL, Origin, Proxy, Request, HTTPResponse
 from ._ssl import default_ssl_context
 from ._sync import (
     ConnectionInterface,
@@ -78,7 +78,7 @@ __all__ = [
     "Origin",
     "URL",
     "Request",
-    "Response",
+    "HTTPResponse",
     "Proxy",
     # async
     "AsyncHTTPConnection",
diff --git a/httpcore2/_api.py b/httpcore2/_api.py
index c7f4f22..2455789 100644
--- a/httpcore2/_api.py
+++ b/httpcore2/_api.py
@@ -4,7 +4,7 @@ import contextlib
 import typing
 from collections.abc import Generator
 
-from ._models import URL, Extensions, HeaderTypes, Response
+from ._models import URL, Extensions, HeaderTypes, HTTPResponse
 from ._sync.connection_pool import ConnectionPool
 
 
@@ -15,7 +15,7 @@ def request(
     headers: HeaderTypes = None,
     content: bytes | typing.Iterator[bytes] | None = None,
     extensions: Extensions | None = None,
-) -> Response:
+) -> HTTPResponse:
     \"\"\"
     Sends an HTTP request, returning the response.
 
@@ -36,7 +36,7 @@ def request(
             Possible keys include `"timeout"`.
 
     Returns:
-        An instance of `httpcore2.Response`.
+        An instance of `httpcore2.HTTPResponse`.
     \"\"\"
     with ConnectionPool() as pool:
         return pool.request(
@@ -56,7 +56,7 @@ def stream(
     headers: HeaderTypes = None,
     content: bytes | typing.Iterator[bytes] | None = None,
     extensions: Extensions | None = None,
-) -> Generator[Response]:
+) -> Generator[HTTPResponse, None, None]:
     \"\"\"
     Sends an HTTP request, returning the response within a content manager.
 
@@ -82,7 +82,7 @@ def stream(
             Possible keys include `"timeout"`.
 
     Returns:
-        An instance of `httpcore2.Response`.
+        An instance of `httpcore2.HTTPResponse`.
     \"\"\"
     with ConnectionPool() as pool:
         with pool.stream(
diff --git a/httpcore2/_async/connection.py b/httpcore2/_async/connection.py
index f0b445e..9cdff1b 100644
--- a/httpcore2/_async/connection.py
+++ b/httpcore2/_async/connection.py
@@ -9,7 +9,7 @@ import typing
 from .._backends.auto import AutoBackend
 from .._backends.base import SOCKET_OPTION, AsyncNetworkBackend, AsyncNetworkStream
 from .._exceptions import ConnectError, ConnectTimeout
-from .._models import Origin, Request, Response
+from .._models import Origin, Request, HTTPResponse
 from .._ssl import default_ssl_context
 from .._synchronization import AsyncLock
 from .._trace import Trace
@@ -64,7 +64,7 @@ class AsyncHTTPConnection(AsyncConnectionInterface):
         self._request_lock = AsyncLock()
         self._socket_options = socket_options
 
-    async def handle_async_request(self, request: Request) -> Response:
+    async def handle_async_request(self, request: Request) -> HTTPResponse:
         if not self.can_handle_request(request.url.origin):
             raise RuntimeError(f"Attempted to send request to {request.url.origin} on connection to {self._origin}")
 
diff --git a/httpcore2/_async/connection_pool.py b/httpcore2/_async/connection_pool.py
index 2c5e398..5de343e 100644
--- a/httpcore2/_async/connection_pool.py
+++ b/httpcore2/_async/connection_pool.py
@@ -9,7 +9,7 @@ from collections.abc import AsyncGenerator
 from .._backends.auto import AutoBackend
 from .._backends.base import SOCKET_OPTION, AsyncNetworkBackend
 from .._exceptions import ConnectionNotAvailable, UnsupportedProtocol
-from .._models import Origin, Proxy, Request, Response
+from .._models import Origin, Proxy, Request, HTTPResponse
 from .._synchronization import AsyncEvent, AsyncShieldCancellation, AsyncThreadLock
 from .._utils import safe_async_iterate
 from .connection import AsyncHTTPConnection
@@ -188,7 +188,7 @@ class AsyncConnectionPool(AsyncRequestInterface):
         \"\"\"
         return list(self._connections)
 
-    async def handle_async_request(self, request: Request) -> Response:
+    async def handle_async_request(self, request: Request) -> HTTPResponse:
         \"\"\"
         Send an HTTP request, and return an HTTP response.
 
@@ -244,7 +244,7 @@ class AsyncConnectionPool(AsyncRequestInterface):
         # Return the response. Note that in this case we still have to manage
         # the point at which the response is closed.
         assert isinstance(response.stream, typing.AsyncIterable)
-        return Response(
+        return HTTPResponse(
             status=response.status,
             headers=response.headers,
             content=PoolByteStream(stream=response.stream, pool_request=pool_request, pool=self),
diff --git a/httpcore2/_async/http11.py b/httpcore2/_async/http11.py
index 2fc9445..714e199 100644
--- a/httpcore2/_async/http11.py
+++ b/httpcore2/_async/http11.py
@@ -18,7 +18,7 @@ from .._exceptions import (
     WriteError,
     map_exceptions,
 )
-from .._models import Origin, Request, Response
+from .._models import Origin, Request, HTTPResponse
 from .._synchronization import AsyncLock, AsyncShieldCancellation
 from .._trace import Trace
 from .._utils import safe_async_iterate
@@ -60,7 +60,7 @@ class AsyncHTTP11Connection(AsyncConnectionInterface):
             max_incomplete_event_size=self.MAX_INCOMPLETE_EVENT_SIZE,
         )
 
-    async def handle_async_request(self, request: Request) -> Response:
+    async def handle_async_request(self, request: Request) -> HTTPResponse:
         if not self.can_handle_request(request.url.origin):
             raise RuntimeError(f"Attempted to send request to {request.url.origin} on connection to {self._origin}")
 
@@ -108,7 +108,7 @@ class AsyncHTTP11Connection(AsyncConnectionInterface):
             if (status == 101) or ((request.method == b"CONNECT") and (200 <= status < 300)):
                 network_stream = AsyncHTTP11UpgradeStream(network_stream, trailing_data)
 
-            return Response(
+            return HTTPResponse(
                 status=status,
                 headers=headers,
                 content=HTTP11ConnectionByteStream(self, request),
@@ -165,7 +165,7 @@ class AsyncHTTP11Connection(AsyncConnectionInterface):
 
         while True:
             event = await self._receive_event(timeout=timeout)
-            if isinstance(event, h11.Response):
+            if isinstance(event, h11.HTTPResponse):
                 break
             if isinstance(event, h11.InformationalResponse) and event.status_code == 101:
                 break
diff --git a/httpcore2/_async/http2.py b/httpcore2/_async/http2.py
index 6f009c1..4bfb73a 100644
--- a/httpcore2/_async/http2.py
+++ b/httpcore2/_async/http2.py
@@ -15,7 +15,7 @@ import h2.settings
 
 from .._backends.base import AsyncNetworkStream
 from .._exceptions import ConnectionNotAvailable, LocalProtocolError, RemoteProtocolError
-from .._models import Origin, Request, Response
+from .._models import Origin, Request, HTTPResponse
 from .._synchronization import AsyncLock, AsyncSemaphore, AsyncShieldCancellation
 from .._trace import Trace
 from .._utils import safe_async_iterate
@@ -72,7 +72,7 @@ class AsyncHTTP2Connection(AsyncConnectionInterface):
         self._read_exception: Exception | None = None
         self._write_exception: Exception | None = None
 
-    async def handle_async_request(self, request: Request) -> Response:
+    async def handle_async_request(self, request: Request) -> HTTPResponse:
         if not self.can_handle_request(request.url.origin):
             # This cannot occur in normal operation, since the connection pool
             # will only send requests on connections that handle them.
@@ -132,7 +132,7 @@ class AsyncHTTP2Connection(AsyncConnectionInterface):
                 status, headers = await self._receive_response(request=request, stream_id=stream_id)
                 trace.return_value = (status, headers)
 
-            return Response(
+            return HTTPResponse(
                 status=status,
                 headers=headers,
                 content=HTTP2ConnectionByteStream(self, request, stream_id=stream_id),
diff --git a/httpcore2/_async/http_proxy.py b/httpcore2/_async/http_proxy.py
index b4aa31c..b10f1d2 100644
--- a/httpcore2/_async/http_proxy.py
+++ b/httpcore2/_async/http_proxy.py
@@ -11,7 +11,7 @@ from .._models import (
     URL,
     Origin,
     Request,
-    Response,
+    HTTPResponse,
     enforce_bytes,
     enforce_headers,
     enforce_url,
@@ -178,7 +178,7 @@ class AsyncForwardHTTPConnection(AsyncConnectionInterface):
         self._proxy_headers = enforce_headers(proxy_headers, name="proxy_headers")
         self._remote_origin = remote_origin
 
-    async def handle_async_request(self, request: Request) -> Response:
+    async def handle_async_request(self, request: Request) -> HTTPResponse:
         headers = merge_headers(self._proxy_headers, request.headers)
         url = URL(
             scheme=self._proxy_origin.scheme,
@@ -255,7 +255,7 @@ class AsyncTunnelHTTPConnection(AsyncConnectionInterface):
         self._connect_lock = AsyncLock()
         self._connected = False
 
-    async def handle_async_request(self, request: Request) -> Response:
+    async def handle_async_request(self, request: Request) -> HTTPResponse:
         timeouts = request.extensions.get("timeout", {})
         timeout = timeouts.get("connect", None)
 
diff --git a/httpcore2/_async/interfaces.py b/httpcore2/_async/interfaces.py
index 008d295..b581980 100644
--- a/httpcore2/_async/interfaces.py
+++ b/httpcore2/_async/interfaces.py
@@ -10,7 +10,7 @@ from .._models import (
     HeaderTypes,
     Origin,
     Request,
-    Response,
+    HTTPResponse,
     enforce_bytes,
     enforce_headers,
     enforce_url,
@@ -27,7 +27,7 @@ class AsyncRequestInterface:
         headers: HeaderTypes = None,
         content: bytes | typing.AsyncIterator[bytes] | None = None,
         extensions: Extensions | None = None,
-    ) -> Response:
+    ) -> HTTPResponse:
         # Strict type checking on our parameters.
         method = enforce_bytes(method, name="method")
         url = enforce_url(url, name="url")
@@ -59,7 +59,7 @@ class AsyncRequestInterface:
         headers: HeaderTypes = None,
         content: bytes | typing.AsyncIterator[bytes] | None = None,
         extensions: Extensions | None = None,
-    ) -> AsyncGenerator[Response]:
+    ) -> AsyncGenerator[HTTPResponse]:
         # Strict type checking on our parameters.
         method = enforce_bytes(method, name="method")
         url = enforce_url(url, name="url")
@@ -81,7 +81,7 @@ class AsyncRequestInterface:
         finally:
             await response.aclose()
 
-    async def handle_async_request(self, request: Request) -> Response:
+    async def handle_async_request(self, request: Request) -> HTTPResponse:
         raise NotImplementedError()  # pragma: no cover
 
 
diff --git a/httpcore2/_async/socks_proxy.py b/httpcore2/_async/socks_proxy.py
index 2bc347b..7927ff8 100644
--- a/httpcore2/_async/socks_proxy.py
+++ b/httpcore2/_async/socks_proxy.py
@@ -8,7 +8,7 @@ import socksio
 from .._backends.auto import AutoBackend
 from .._backends.base import AsyncNetworkBackend, AsyncNetworkStream
 from .._exceptions import ConnectionNotAvailable, ProxyError
-from .._models import URL, Origin, Request, Response, enforce_bytes, enforce_url
+from .._models import URL, Origin, Request, HTTPResponse, enforce_bytes, enforce_url
 from .._ssl import default_ssl_context
 from .._synchronization import AsyncLock
 from .._trace import Trace
@@ -209,7 +209,7 @@ class AsyncSocks5Connection(AsyncConnectionInterface):
         self._connection: AsyncConnectionInterface | None = None
         self._connect_failed = False
 
-    async def handle_async_request(self, request: Request) -> Response:
+    async def handle_async_request(self, request: Request) -> HTTPResponse:
         timeouts = request.extensions.get("timeout", {})
         sni_hostname = request.extensions.get("sni_hostname", None)
         timeout = timeouts.get("connect", None)
diff --git a/httpcore2/_models.py b/httpcore2/_models.py
index c8e4f0c..c6fc935 100644
--- a/httpcore2/_models.py
+++ b/httpcore2/_models.py
@@ -349,7 +349,7 @@ class Request:
         return f"<{self.__class__.__name__} [{self.method!r}]>"
 
 
-class Response:
+class HTTPResponse:  # public alias kept as Response until 2.0
     \"\"\"
     An HTTP response.
     \"\"\"
diff --git a/httpcore2/_sync/connection.py b/httpcore2/_sync/connection.py
index 6691a9f..963007b 100644
--- a/httpcore2/_sync/connection.py
+++ b/httpcore2/_sync/connection.py
@@ -9,7 +9,7 @@ import typing
 from .._backends.sync import SyncBackend
 from .._backends.base import SOCKET_OPTION, NetworkBackend, NetworkStream
 from .._exceptions import ConnectError, ConnectTimeout
-from .._models import Origin, Request, Response
+from .._models import Origin, Request, HTTPResponse
 from .._ssl import default_ssl_context
 from .._synchronization import Lock
 from .._trace import Trace
@@ -64,7 +64,7 @@ class HTTPConnection(ConnectionInterface):
         self._request_lock = Lock()
         self._socket_options = socket_options
 
-    def handle_request(self, request: Request) -> Response:
+    def handle_request(self, request: Request) -> HTTPResponse:
         if not self.can_handle_request(request.url.origin):
             raise RuntimeError(f"Attempted to send request to {request.url.origin} on connection to {self._origin}")
 
diff --git a/httpcore2/_sync/connection_pool.py b/httpcore2/_sync/connection_pool.py
index a6051ba..f2ed1ab 100644
--- a/httpcore2/_sync/connection_pool.py
+++ b/httpcore2/_sync/connection_pool.py
@@ -9,7 +9,7 @@ from collections.abc import Generator
 from .._backends.sync import SyncBackend
 from .._backends.base import SOCKET_OPTION, NetworkBackend
 from .._exceptions import ConnectionNotAvailable, UnsupportedProtocol
-from .._models import Origin, Proxy, Request, Response
+from .._models import Origin, Proxy, Request, HTTPResponse
 from .._synchronization import Event, ShieldCancellation, ThreadLock
 from .._utils import safe_iterate
 from .connection import HTTPConnection
@@ -188,7 +188,7 @@ class ConnectionPool(RequestInterface):
         \"\"\"
         return list(self._connections)
 
-    def handle_request(self, request: Request) -> Response:
+    def handle_request(self, request: Request) -> HTTPResponse:
         \"\"\"
         Send an HTTP request, and return an HTTP response.
 
@@ -244,7 +244,7 @@ class ConnectionPool(RequestInterface):
         # Return the response. Note that in this case we still have to manage
         # the point at which the response is closed.
         assert isinstance(response.stream, typing.Iterable)
-        return Response(
+        return HTTPResponse(
             status=response.status,
             headers=response.headers,
             content=PoolByteStream(stream=response.stream, pool_request=pool_request, pool=self),
diff --git a/httpcore2/_sync/http11.py b/httpcore2/_sync/http11.py
index 50bce83..8d42c9e 100644
--- a/httpcore2/_sync/http11.py
+++ b/httpcore2/_sync/http11.py
@@ -18,7 +18,7 @@ from .._exceptions import (
     WriteError,
     map_exceptions,
 )
-from .._models import Origin, Request, Response
+from .._models import Origin, Request, HTTPResponse
 from .._synchronization import Lock, ShieldCancellation
 from .._trace import Trace
 from .._utils import safe_iterate
@@ -60,7 +60,7 @@ class HTTP11Connection(ConnectionInterface):
             max_incomplete_event_size=self.MAX_INCOMPLETE_EVENT_SIZE,
         )
 
-    def handle_request(self, request: Request) -> Response:
+    def handle_request(self, request: Request) -> HTTPResponse:
         if not self.can_handle_request(request.url.origin):
             raise RuntimeError(f"Attempted to send request to {request.url.origin} on connection to {self._origin}")
 
@@ -108,7 +108,7 @@ class HTTP11Connection(ConnectionInterface):
             if (status == 101) or ((request.method == b"CONNECT") and (200 <= status < 300)):
                 network_stream = HTTP11UpgradeStream(network_stream, trailing_data)
 
-            return Response(
+            return HTTPResponse(
                 status=status,
                 headers=headers,
                 content=HTTP11ConnectionByteStream(self, request),
@@ -165,7 +165,7 @@ class HTTP11Connection(ConnectionInterface):
 
         while True:
             event = self._receive_event(timeout=timeout)
-            if isinstance(event, h11.Response):
+            if isinstance(event, h11.HTTPResponse):
                 break
             if isinstance(event, h11.InformationalResponse) and event.status_code == 101:
                 break
diff --git a/httpcore2/_sync/http2.py b/httpcore2/_sync/http2.py
index ee314c8..701870f 100644
--- a/httpcore2/_sync/http2.py
+++ b/httpcore2/_sync/http2.py
@@ -15,7 +15,7 @@ import h2.settings
 
 from .._backends.base import NetworkStream
 from .._exceptions import ConnectionNotAvailable, LocalProtocolError, RemoteProtocolError
-from .._models import Origin, Request, Response
+from .._models import Origin, Request, HTTPResponse
 from .._synchronization import Lock, Semaphore, ShieldCancellation
 from .._trace import Trace
 from .._utils import safe_iterate
@@ -72,7 +72,7 @@ class HTTP2Connection(ConnectionInterface):
         self._read_exception: Exception | None = None
         self._write_exception: Exception | None = None
 
-    def handle_request(self, request: Request) -> Response:
+    def handle_request(self, request: Request) -> HTTPResponse:
         if not self.can_handle_request(request.url.origin):
             # This cannot occur in normal operation, since the connection pool
             # will only send requests on connections that handle them.
@@ -132,7 +132,7 @@ class HTTP2Connection(ConnectionInterface):
                 status, headers = self._receive_response(request=request, stream_id=stream_id)
                 trace.return_value = (status, headers)
 
-            return Response(
+            return HTTPResponse(
                 status=status,
                 headers=headers,
                 content=HTTP2ConnectionByteStream(self, request, stream_id=stream_id),
diff --git a/httpcore2/_sync/http_proxy.py b/httpcore2/_sync/http_proxy.py
index f7f6677..79fefa9 100644
--- a/httpcore2/_sync/http_proxy.py
+++ b/httpcore2/_sync/http_proxy.py
@@ -11,7 +11,7 @@ from .._models import (
     URL,
     Origin,
     Request,
-    Response,
+    HTTPResponse,
     enforce_bytes,
     enforce_headers,
     enforce_url,
@@ -178,7 +178,7 @@ class ForwardHTTPConnection(ConnectionInterface):
         self._proxy_headers = enforce_headers(proxy_headers, name="proxy_headers")
         self._remote_origin = remote_origin
 
-    def handle_request(self, request: Request) -> Response:
+    def handle_request(self, request: Request) -> HTTPResponse:
         headers = merge_headers(self._proxy_headers, request.headers)
         url = URL(
             scheme=self._proxy_origin.scheme,
@@ -255,7 +255,7 @@ class TunnelHTTPConnection(ConnectionInterface):
         self._connect_lock = Lock()
         self._connected = False
 
-    def handle_request(self, request: Request) -> Response:
+    def handle_request(self, request: Request) -> HTTPResponse:
         timeouts = request.extensions.get("timeout", {})
         timeout = timeouts.get("connect", None)
 
diff --git a/httpcore2/_sync/interfaces.py b/httpcore2/_sync/interfaces.py
index 0e66bd1..27c7cf1 100644
--- a/httpcore2/_sync/interfaces.py
+++ b/httpcore2/_sync/interfaces.py
@@ -10,7 +10,7 @@ from .._models import (
     HeaderTypes,
     Origin,
     Request,
-    Response,
+    HTTPResponse,
     enforce_bytes,
     enforce_headers,
     enforce_url,
@@ -27,7 +27,7 @@ class RequestInterface:
         headers: HeaderTypes = None,
         content: bytes | typing.Iterator[bytes] | None = None,
         extensions: Extensions | None = None,
-    ) -> Response:
+    ) -> HTTPResponse:
         # Strict type checking on our parameters.
         method = enforce_bytes(method, name="method")
         url = enforce_url(url, name="url")
@@ -59,7 +59,7 @@ class RequestInterface:
         headers: HeaderTypes = None,
         content: bytes | typing.Iterator[bytes] | None = None,
         extensions: Extensions | None = None,
-    ) -> Generator[Response]:
+    ) -> Generator[HTTPResponse]:
         # Strict type checking on our parameters.
         method = enforce_bytes(method, name="method")
         url = enforce_url(url, name="url")
@@ -81,7 +81,7 @@ class RequestInterface:
         finally:
             response.close()
 
-    def handle_request(self, request: Request) -> Response:
+    def handle_request(self, request: Request) -> HTTPResponse:
         raise NotImplementedError()  # pragma: no cover
 
 
diff --git a/httpcore2/_sync/socks_proxy.py b/httpcore2/_sync/socks_proxy.py
index b49a576..2ec2472 100644
--- a/httpcore2/_sync/socks_proxy.py
+++ b/httpcore2/_sync/socks_proxy.py
@@ -8,7 +8,7 @@ import socksio
 from .._backends.sync import SyncBackend
 from .._backends.base import NetworkBackend, NetworkStream
 from .._exceptions import ConnectionNotAvailable, ProxyError
-from .._models import URL, Origin, Request, Response, enforce_bytes, enforce_url
+from .._models import URL, Origin, Request, HTTPResponse, enforce_bytes, enforce_url
 from .._ssl import default_ssl_context
 from .._synchronization import Lock
 from .._trace import Trace
@@ -209,7 +209,7 @@ class Socks5Connection(ConnectionInterface):
         self._connection: ConnectionInterface | None = None
         self._connect_failed = False
 
-    def handle_request(self, request: Request) -> Response:
+    def handle_request(self, request: Request) -> HTTPResponse:
         timeouts = request.extensions.get("timeout", {})
         sni_hostname = request.extensions.get("sni_hostname", None)
         timeout = timeouts.get("connect", None)
"""

# real: git diff in a scratch repo with .gitattributes (linguist-generated, -diff), a lockfile, a snapshot and a .pb.go
GEN_DIFF = """\
diff --git a/api/v1/orders.pb.go b/api/v1/orders.pb.go
index dec7cf6..8627929 100644
--- a/api/v1/orders.pb.go
+++ b/api/v1/orders.pb.go
@@ -1,51 +1,51 @@
 // Code generated by protoc-gen-go. DO NOT EDIT.
 package v1
 
-func (x *Order0) GetId() int64 {
+func (x *Order0) GetOrderId() int64 {
 \tif x != nil {
-\t\treturn x.Id
+\t\treturn x.OrderId
 \t}
 \treturn 0
 }
 
-func (x *Order1) GetId() int64 {
+func (x *Order1) GetOrderId() int64 {
 \tif x != nil {
-\t\treturn x.Id
+\t\treturn x.OrderId
 \t}
 \treturn 0
 }
 
-func (x *Order2) GetId() int64 {
+func (x *Order2) GetOrderId() int64 {
 \tif x != nil {
-\t\treturn x.Id
+\t\treturn x.OrderId
 \t}
 \treturn 0
 }
 
-func (x *Order3) GetId() int64 {
+func (x *Order3) GetOrderId() int64 {
 \tif x != nil {
-\t\treturn x.Id
+\t\treturn x.OrderId
 \t}
 \treturn 0
 }
 
-func (x *Order4) GetId() int64 {
+func (x *Order4) GetOrderId() int64 {
 \tif x != nil {
-\t\treturn x.Id
+\t\treturn x.OrderId
 \t}
 \treturn 0
 }
 
-func (x *Order5) GetId() int64 {
+func (x *Order5) GetOrderId() int64 {
 \tif x != nil {
-\t\treturn x.Id
+\t\treturn x.OrderId
 \t}
 \treturn 0
 }
 
-func (x *Order6) GetId() int64 {
+func (x *Order6) GetOrderId() int64 {
 \tif x != nil {
-\t\treturn x.Id
+\t\treturn x.OrderId
 \t}
 \treturn 0
 }
diff --git a/dist/bundle.min.js b/dist/bundle.min.js
index 25aada9..aca1ec5 100644
--- a/dist/bundle.min.js
+++ b/dist/bundle.min.js
@@ -1 +1 @@
-!function(){var a0=function(e,n){return e.map(function(r){return r*n+0})};console.log(a0(0))}();!function(){var a1=function(e,n){return e.map(function(r){return r*n+1})};console.log(a1(1))}();!function(){var a2=function(e,n){return e.map(function(r){return r*n+2})};console.log(a2(2))}();!function(){var a3=function(e,n){return e.map(function(r){return r*n+3})};console.log(a3(3))}();!function(){var a4=function(e,n){return e.map(function(r){return r*n+4})};console.log(a4(4))}();!function(){var a5=function(e,n){return e.map(function(r){return r*n+5})};console.log(a5(5))}();!function(){var a6=function(e,n){return e.map(function(r){return r*n+6})};console.log(a6(6))}();!function(){var a7=function(e,n){return e.map(function(r){return r*n+7})};console.log(a7(7))}();!function(){var a8=function(e,n){return e.map(function(r){return r*n+8})};console.log(a8(8))}();!function(){var a9=function(e,n){return e.map(function(r){return r*n+9})};console.log(a9(9))}();!function(){var a10=function(e,n){return e.map(function(r){return r*n+10})};console.log(a10(10))}();!function(){var a11=function(e,n){return e.map(function(r){return r*n+11})};console.log(a11(11))}();!function(){var a12=function(e,n){return e.map(function(r){return r*n+12})};console.log(a12(12))}();!function(){var a13=function(e,n){return e.map(function(r){return r*n+13})};console.log(a13(13))}();
+!function(){var a0=function(e,n){return e.map(function(r){return r*n+1+0})};console.log(a0(0))}();!function(){var a1=function(e,n){return e.map(function(r){return r*n+1+1})};console.log(a1(1))}();!function(){var a2=function(e,n){return e.map(function(r){return r*n+1+2})};console.log(a2(2))}();!function(){var a3=function(e,n){return e.map(function(r){return r*n+1+3})};console.log(a3(3))}();!function(){var a4=function(e,n){return e.map(function(r){return r*n+1+4})};console.log(a4(4))}();!function(){var a5=function(e,n){return e.map(function(r){return r*n+1+5})};console.log(a5(5))}();!function(){var a6=function(e,n){return e.map(function(r){return r*n+1+6})};console.log(a6(6))}();!function(){var a7=function(e,n){return e.map(function(r){return r*n+1+7})};console.log(a7(7))}();!function(){var a8=function(e,n){return e.map(function(r){return r*n+1+8})};console.log(a8(8))}();!function(){var a9=function(e,n){return e.map(function(r){return r*n+1+9})};console.log(a9(9))}();!function(){var a10=function(e,n){return e.map(function(r){return r*n+1+10})};console.log(a10(10))}();!function(){var a11=function(e,n){return e.map(function(r){return r*n+1+11})};console.log(a11(11))}();!function(){var a12=function(e,n){return e.map(function(r){return r*n+1+12})};console.log(a12(12))}();!function(){var a13=function(e,n){return e.map(function(r){return r*n+1+13})};console.log(a13(13))}();
diff --git a/docs/arch.svg b/docs/arch.svg
index d9a5154..b5717c4 100644
Binary files a/docs/arch.svg and b/docs/arch.svg differ
diff --git a/package-lock.json b/package-lock.json
index 5275cce..a6704e3 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -3,29 +3,29 @@
   "lockfileVersion": 3,
   "packages": {
     "node_modules/p0": {
-      "version": "1.0.0",
-      "resolved": "https://registry.npmjs.org/p0/-/p0-1.0.0.tgz",
-      "integrity": "sha512-d76d4330f1446beab0c11fdecb91ce375bc8fbbcbde5c0994164d8399f767c45"
+      "version": "1.1.0",
+      "resolved": "https://registry.npmjs.org/p0/-/p0-1.1.0.tgz",
+      "integrity": "sha512-096d373742f9a039c320a4737c2b3abe14a03569d26b949692e5dfe8cb1855fe"
     },
     "node_modules/p1": {
-      "version": "1.0.1",
-      "resolved": "https://registry.npmjs.org/p1/-/p1-1.0.1.tgz",
-      "integrity": "sha512-c6a5387777330bdbd7210dff076ce2ef87b0b125ec1d7da0a6eb8c9ebd69fe29"
+      "version": "1.1.0",
+      "resolved": "https://registry.npmjs.org/p1/-/p1-1.1.1.tgz",
+      "integrity": "sha512-bc1e3ac1c27db4ecf72c2c26786295229623d7cfa9ae7a34254499c7001d9a88"
     },
     "node_modules/p2": {
-      "version": "1.0.2",
-      "resolved": "https://registry.npmjs.org/p2/-/p2-1.0.2.tgz",
-      "integrity": "sha512-5f2dd97f1cfb10f62827688de6a16a3b0d464138a62332553fc1ea36f17fd374"
+      "version": "1.1.0",
+      "resolved": "https://registry.npmjs.org/p2/-/p2-1.1.2.tgz",
+      "integrity": "sha512-32b7228fcd4a55577d24b39645cf8aa4059a91e1c527e27951c342505f877031"
     },
     "node_modules/p3": {
-      "version": "1.0.3",
-      "resolved": "https://registry.npmjs.org/p3/-/p3-1.0.3.tgz",
-      "integrity": "sha512-3fd4235992edcf451a1afe878b33e968617959ce3f1f65a8de5271007814e8a2"
+      "version": "1.1.0",
+      "resolved": "https://registry.npmjs.org/p3/-/p3-1.1.3.tgz",
+      "integrity": "sha512-181e290aae9af1698a0c510089ce5ef7e91b4ad169fc5360df5ca32ebad5ccc2"
     },
     "node_modules/p4": {
-      "version": "1.0.4",
-      "resolved": "https://registry.npmjs.org/p4/-/p4-1.0.4.tgz",
-      "integrity": "sha512-de11cc9dea959c212e9c82b1478c281d687c966c377b9aa2bb2edb20035b7399"
+      "version": "1.1.0",
+      "resolved": "https://registry.npmjs.org/p4/-/p4-1.1.4.tgz",
+      "integrity": "sha512-a9b3d1a243f9300cba98666ace1c9c17b313fc7e8db9b92c903c2ac9316774fe"
     },
     "node_modules/p5": {
       "version": "1.0.5",
diff --git a/src/server.py b/src/server.py
index ff899dd..f7b5837 100644
--- a/src/server.py
+++ b/src/server.py
@@ -6,7 +6,7 @@ from flask import Flask, jsonify, request
 app = Flask(__name__)
 log = logging.getLogger(__name__)
 
-DEFAULT_PAGE_SIZE = 20
+DEFAULT_PAGE_SIZE = 50
 
 
 @app.route("/orders")
@@ -14,6 +14,7 @@ def list_orders():
     page = int(request.args.get("page", 1))
     size = int(request.args.get("size", DEFAULT_PAGE_SIZE))
     rows = db.query("select * from orders order by id limit ? offset ?", size, (page - 1) * size)
+    log.debug("listing %d orders", len(rows))
     return jsonify(rows)
 
 
@@ -26,4 +27,5 @@ def get_order(order_id):
 
 
 def health():
+    log.info("health check")
     return "ok"
diff --git a/tests/__snapshots__/orders.test.js.snap b/tests/__snapshots__/orders.test.js.snap
index 2b34eaa..1a51497 100644
--- a/tests/__snapshots__/orders.test.js.snap
+++ b/tests/__snapshots__/orders.test.js.snap
@@ -1,35 +1,35 @@
 // Jest Snapshot v1, https://goo.gl/fbAQLP
 
 exports[`renders order 0 1`] = `
-<div className="order">
+<div className="order-row">
   <span>#0</span>
   <span>$10.00</span>
 </div>
 `;
 
 exports[`renders order 1 1`] = `
-<div className="order">
+<div className="order-row">
   <span>#1</span>
   <span>$11.00</span>
 </div>
 `;
 
 exports[`renders order 2 1`] = `
-<div className="order">
+<div className="order-row">
   <span>#2</span>
   <span>$12.00</span>
 </div>
 `;
 
 exports[`renders order 3 1`] = `
-<div className="order">
+<div className="order-row">
   <span>#3</span>
   <span>$13.00</span>
 </div>
 `;
 
 exports[`renders order 4 1`] = `
-<div className="order">
+<div className="order-row">
   <span>#4</span>
   <span>$14.00</span>
 </div>
diff --git a/tests/test_server.py b/tests/test_server.py
new file mode 100644
index 0000000..cb02f24
--- /dev/null
+++ b/tests/test_server.py
@@ -0,0 +1,5 @@
+from src.server import app
+
+
+def test_health():
+    assert app.test_client().get("/health").status_code == 200
"""

# real: python3 -m cProfile -s tottime on a small search program (first 168 rows; the rest are all 0.000)
CPROFILE = """\
7800
         864284 function calls (863207 primitive calls) in 0.474 seconds

   Ordered by: internal time

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
      300    0.135    0.000    0.171    0.001 work.py:14(score)
    97500    0.058    0.000    0.088    0.000 random.py:245(_randbelow_with_getrandbits)
    49500    0.039    0.000    0.100    0.000 random.py:345(choice)
    48400    0.034    0.000    0.197    0.000 work.py:24(<genexpr>)
    48000    0.025    0.000    0.070    0.000 random.py:295(randrange)
   145896    0.017    0.000    0.017    0.000 {method 'getrandbits' of '_random.Random' objects}
      300    0.017    0.000    0.018    0.000 heapq.py:523(nlargest)
103552/103384    0.013    0.000    0.013    0.000 {built-in method builtins.len}
    97504    0.013    0.000    0.013    0.000 {method 'bit_length' of 'int' objects}
   120000    0.012    0.000    0.012    0.000 __init__.py:616(__missing__)
     1839    0.011    0.000    0.215    0.000 {method 'join' of 'str' objects}
        1    0.010    0.010    0.024    0.024 work.py:7(build_index)
       60    0.008    0.000    0.008    0.000 {built-in method marshal.loads}
      700    0.007    0.000    0.007    0.000 {method 'findall' of 're.Pattern' objects}
    48046    0.006    0.000    0.006    0.000 {method 'add' of 'set' objects}
    48000    0.006    0.000    0.006    0.000 {built-in method _operator.index}
     2277    0.004    0.000    0.004    0.000 {method 'get' of 'dict' objects}
  260/258    0.004    0.000    0.009    0.000 {built-in method builtins.__build_class__}
      103    0.003    0.000    0.003    0.000 <frozen importlib._bootstrap>:79(__init__)
       16    0.002    0.000    0.005    0.000 enum.py:1755(convert_class)
    96/36    0.002    0.000    0.004    0.000 _parser.py:511(_parse)
       21    0.002    0.000    0.002    0.000 {built-in method _imp.exec_builtin}
        1    0.002    0.002    0.413    0.413 work.py:21(main)
      351    0.001    0.000    0.001    0.000 {built-in method posix.stat}
       60    0.001    0.000    0.001    0.000 {method 'read' of '_io.BufferedReader' objects}
      186    0.001    0.000    0.005    0.000 <frozen importlib._bootstrap_external>:1625(find_spec)
      999    0.001    0.000    0.002    0.000 <frozen importlib._bootstrap_external>:131(_path_join)
      300    0.001    0.000    0.019    0.000 __init__.py:625(most_common)
       60    0.001    0.000    0.001    0.000 {built-in method _io.open_code}
  417/413    0.001    0.000    0.001    0.000 {built-in method __new__ of type object at 0x1320620}
      838    0.001    0.000    0.009    0.000 __init__.py:330(_compile)
       91    0.001    0.000    0.009    0.000 <frozen importlib._bootstrap>:1240(_find_spec)
     1800    0.001    0.000    0.008    0.000 work.py:28(<genexpr>)
     3532    0.001    0.000    0.001    0.000 {built-in method builtins.isinstance}
       10    0.001    0.000    0.005    0.000 enum.py:900(_convert_)
   162/33    0.001    0.000    0.002    0.000 _compiler.py:37(_compile)
      103    0.001    0.000    0.001    0.000 <frozen importlib._bootstrap>:74(__new__)
       60    0.001    0.000    0.009    0.000 <frozen importlib._bootstrap_external>:779(_compile_bytecode)
       72    0.001    0.000    0.001    0.000 _compiler.py:241(_optimize_charset)
      300    0.001    0.000    0.001    0.000 __init__.py:602(__init__)
     3360    0.001    0.000    0.001    0.000 {method 'append' of 'list' objects}
      103    0.001    0.000    0.005    0.000 <frozen importlib._bootstrap>:304(acquire)
      700    0.001    0.000    0.009    0.000 __init__.py:270(findall)
      700    0.001    0.000    0.010    0.000 work.py:4(tokenize)
        8    0.001    0.000    0.001    0.000 {built-in method builtins.eval}
     91/8    0.000    0.000    0.061    0.008 <frozen importlib._bootstrap>:1358(_find_and_load)
      703    0.000    0.000    0.000    0.000 {method 'lower' of 'str' objects}
       60    0.000    0.000    0.013    0.000 <frozen importlib._bootstrap_external>:1094(get_code)
        8    0.000    0.000    0.001    0.000 __init__.py:358(namedtuple)
        1    0.000    0.000    0.000    0.000 encoder.py:207(iterencode)
      120    0.000    0.000    0.001    0.000 <frozen importlib._bootstrap_external>:509(cache_from_source)
      324    0.000    0.000    0.000    0.000 enum.py:831(__setattr__)
     2118    0.000    0.000    0.000    0.000 {method 'rstrip' of 'str' objects}
       17    0.000    0.000    0.001    0.000 enum.py:499(__new__)
     2569    0.000    0.000    0.000    0.000 {method 'startswith' of 'str' objects}
       68    0.000    0.000    0.006    0.000 <frozen importlib._bootstrap_external>:1523(_get_spec)
     91/8    0.000    0.000    0.060    0.008 <frozen importlib._bootstrap>:1306(_find_and_load_unlocked)
      103    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap>:426(_get_module_lock)
      801    0.000    0.000    0.000    0.000 _parser.py:167(__getitem__)
     1046    0.000    0.000    0.001    0.000 _parser.py:260(get)
    83/11    0.000    0.000    0.058    0.005 <frozen importlib._bootstrap>:911(_load_unlocked)
       83    0.000    0.000    0.001    0.000 <frozen importlib._bootstrap>:733(_init_module_attrs)
    74/33    0.000    0.000    0.004    0.000 _parser.py:451(_parse_sub)
      940    0.000    0.000    0.000    0.000 {built-in method builtins.getattr}
     1317    0.000    0.000    0.000    0.000 _parser.py:239(__next)
   190/19    0.000    0.000    0.056    0.003 <frozen importlib._bootstrap>:480(_call_with_frames_removed)
   195/68    0.000    0.000    0.000    0.000 _parser.py:177(getwidth)
      103    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap>:372(release)
       21    0.000    0.000    0.000    0.000 {built-in method _imp.create_builtin}
      310    0.000    0.000    0.000    0.000 {method 'sort' of 'list' objects}
     62/1    0.000    0.000    0.473    0.473 {built-in method builtins.exec}
        9    0.000    0.000    0.032    0.004 __init__.py:1(<module>)
     1642    0.000    0.000    0.000    0.000 {method 'isupper' of 'str' objects}
      279    0.000    0.000    0.000    0.000 enum.py:37(_is_descriptor)
        2    0.000    0.000    0.000    0.000 {built-in method _imp.create_dynamic}
       61    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap_external>:830(spec_from_file_location)
      103    0.000    0.000    0.004    0.000 <frozen importlib._bootstrap>:124(setdefault)
        1    0.000    0.000    0.473    0.473 work.py:1(<module>)
       33    0.000    0.000    0.008    0.000 _compiler.py:743(compile)
      285    0.000    0.000    0.000    0.000 enum.py:78(_is_private)
     1067    0.000    0.000    0.000    0.000 {built-in method builtins.hasattr}
       33    0.000    0.000    0.001    0.000 _compiler.py:514(_compile_info)
      121    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap_external>:137(_path_split)
      419    0.000    0.000    0.001    0.000 {built-in method builtins.setattr}
       90    0.000    0.000    0.000    0.000 __init__.py:89(find_spec)
     1039    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap>:491(_verbose_message)
       60    0.000    0.000    0.003    0.000 <frozen importlib._bootstrap_external>:1215(get_data)
      410    0.000    0.000    0.000    0.000 socket.py:84(<lambda>)
        9    0.000    0.000    0.000    0.000 {built-in method posix.listdir}
       60    0.000    0.000    0.000    0.000 {method '__exit__' of '_io._IOBase' objects}
      351    0.000    0.000    0.002    0.000 <frozen importlib._bootstrap_external>:145(_path_stat)
       83    0.000    0.000    0.002    0.000 <frozen importlib._bootstrap>:806(module_from_spec)
      412    0.000    0.000    0.000    0.000 socket.py:94(<lambda>)
      409    0.000    0.000    0.000    0.000 socket.py:79(<lambda>)
      411    0.000    0.000    0.000    0.000 socket.py:89(<lambda>)
      331    0.000    0.000    0.000    0.000 enum.py:47(_is_dunder)
    60/11    0.000    0.000    0.058    0.005 <frozen importlib._bootstrap_external>:1021(exec_module)
      408    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap>:1226(__exit__)
      497    0.000    0.000    0.000    0.000 {method 'rpartition' of 'str' objects}
        1    0.000    0.000    0.005    0.005 socket.py:1(<module>)
      300    0.000    0.000    0.000    0.000 {built-in method _heapq.heapify}
       35    0.000    0.000    0.000    0.000 ipaddress.py:1661(_ip_int_from_string)
      180    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap_external>:89(_unpack_uint32)
      324    0.000    0.000    0.000    0.000 _parser.py:163(__len__)
      365    0.000    0.000    0.000    0.000 {method 'items' of 'dict' objects}
      408    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap>:1222(__enter__)
      300    0.000    0.000    0.000    0.000 {built-in method builtins.iter}
      160    0.000    0.000    0.000    0.000 {built-in method builtins.max}
      103    0.000    0.000    0.004    0.000 <frozen importlib._bootstrap>:162(__enter__)
        1    0.000    0.000    0.000    0.000 {built-in method _imp.get_frozen_object}
      229    0.000    0.000    0.001    0.000 <frozen importlib._bootstrap_external>:1501(_path_importer_cache)
       33    0.000    0.000    0.005    0.000 _parser.py:970(parse)
       33    0.000    0.000    0.000    0.000 enum.py:1609(__and__)
       23    0.000    0.000    0.000    0.000 _compiler.py:389(_mk_bitmap)
        3    0.000    0.000    0.000    0.000 {built-in method builtins.dir}
      502    0.000    0.000    0.000    0.000 _parser.py:255(match)
       88    0.000    0.000    0.000    0.000 ipaddress.py:1213(_parse_octet)
       87    0.000    0.000    0.001    0.000 <frozen importlib._bootstrap_external>:164(_path_isfile)
       91    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap>:445(cb)
      300    0.000    0.000    0.000    0.000 __init__.py:676(update)
       60    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap_external>:727(_validate_timestamp_pyc)
      602    0.000    0.000    0.000    0.000 {built-in method _imp.release_lock}
       43    0.000    0.000    0.000    0.000 {built-in method posix.getcwd}
      602    0.000    0.000    0.000    0.000 {built-in method _imp.acquire_lock}
       60    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap_external>:694(_classify_pyc)
       65    0.000    0.000    0.000    0.000 {built-in method builtins.locals}
       91    0.000    0.000    0.005    0.000 <frozen importlib._bootstrap>:416(__enter__)
      285    0.000    0.000    0.000    0.000 enum.py:58(_is_sunder)
       60    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap_external>:1620(_get_spec)
       72    0.000    0.000    0.000    0.000 _compiler.py:214(_compile_charset)
    40/30    0.000    0.000    0.000    0.000 _compiler.py:439(_get_literal_prefix)
      101    0.000    0.000    0.000    0.000 {method 'format' of 'str' objects}
       61    0.000    0.000    0.001    0.000 <frozen importlib._bootstrap_external>:639(_get_cached)
       91    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap>:232(__init__)
      242    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap_external>:139(<genexpr>)
        1    0.000    0.000    0.005    0.005 ssl.py:1(<module>)
      219    0.000    0.000    0.000    0.000 _parser.py:292(tell)
        1    0.000    0.000    0.001    0.001 quoprimime.py:1(<module>)
      103    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap>:82(remove)
       35    0.000    0.000    0.001    0.000 ipaddress.py:2320(__init__)
      403    0.000    0.000    0.000    0.000 {method 'get' of 'mappingproxy' objects}
       18    0.000    0.000    0.000    0.000 {built-in method _abc._abc_init}
       90    0.000    0.000    0.000    0.000 {built-in method _imp.is_builtin}
       33    0.000    0.000    0.003    0.000 _compiler.py:576(_code)
       52    0.000    0.000    0.000    0.000 ipaddress.py:1943(__init__)
       60    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap_external>:671(_check_name_wrapper)
       53    0.000    0.000    0.000    0.000 _compiler.py:401(_simple)
      117    0.000    0.000    0.000    0.000 enum.py:1591(_get_value)
       96    0.000    0.000    0.001    0.000 <frozen importlib._bootstrap_external>:155(_path_is_mode_type)
      202    0.000    0.000    0.000    0.000 {built-in method from_bytes}
       90    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap>:982(find_spec)
      357    0.000    0.000    0.000    0.000 {built-in method builtins.min}
      209    0.000    0.000    0.000    0.000 {method '__exit__' of '_thread.RLock' objects}
        1    0.000    0.000    0.001    0.001 pickle.py:1(<module>)
        1    0.000    0.000    0.000    0.000 _compat_pickle.py:1(<module>)
      260    0.000    0.000    0.000    0.000 {method 'setdefault' of 'dict' objects}
      190    0.000    0.000    0.000    0.000 _parser.py:175(append)
       26    0.000    0.000    0.000    0.000 ipaddress.py:474(_prefix_from_prefix_string)
       22    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap>:662(spec_from_loader)
       33    0.000    0.000    0.000    0.000 _parser.py:230(__init__)
        1    0.000    0.000    0.003    0.003 parse.py:1(<module>)
       68    0.000    0.000    0.006    0.000 <frozen importlib._bootstrap_external>:1552(find_spec)
       56    0.000    0.000    0.000    0.000 ipaddress.py:533(_split_addr_prefix)
      121    0.000    0.000    0.001    0.000 <frozen importlib._bootstrap>:632(cached)
      280    0.000    0.000    0.000    0.000 {method 'find' of 'bytearray' objects}
       91    0.000    0.000    0.000    0.000 <frozen importlib._bootstrap>:420(__exit__)
        1    0.000    0.000    0.001    0.001 dbapi2.py:1(<module>)
        1    0.000    0.000    0.003    0.003 shutil.py:1(<module>)
       21    0.000    0.000    0.000    0.000 ipaddress.py:1512(__init__)
"""

# real: jmap -histo <pid> of a JDK 21 process holding 20k small maps
HISTO = """\
 num     #instances         #bytes  class name (module)
-------------------------------------------------------
   1:        108522        2795376  [B (java.base@21.0.12.1)
   2:         88176        2116224  java.lang.String (java.base@21.0.12.1)
   3:         22348        1661520  [I (java.base@21.0.12.1)
   4:          1584        1431448  [Ljava.lang.Object; (java.base@21.0.12.1)
   5:            48        1181640  [Ljdk.internal.vm.FillerElement; (java.base@21.0.12.1)
   6:         20265         972720  java.util.HashMap (java.base@21.0.12.1)
   7:         21023         672736  java.util.HashMap$Node (java.base@21.0.12.1)
   8:         20000         640000  java.util.ImmutableCollections$Map1 (java.base@21.0.12.1)
   9:         20264         504640  [Ljava.util.HashMap$Node; (java.base@21.0.12.1)
  10:         19932         478368  java.util.ImmutableCollections$Set12 (java.base@21.0.12.1)
  11:         19699         472776  java.util.KeyValueHolder (java.base@21.0.12.1)
  12:         19690         472560  java.util.ImmutableCollections$Set12$1 (java.base@21.0.12.1)
  13:         20001         320016  java.util.Optional (java.base@21.0.12.1)
  14:          1505         186184  java.lang.Class (java.base@21.0.12.1)
  15:          1071          34272  java.util.concurrent.ConcurrentHashMap$Node (java.base@21.0.12.1)
  16:             8          33560  [C (java.base@21.0.12.1)
  17:            20          23296  [Ljava.util.concurrent.ConcurrentHashMap$Node; (java.base@21.0.12.1)
  18:           307          17192  jdk.internal.org.objectweb.asm.SymbolTable$Entry (java.base@21.0.12.1)
  19:           371          11064  [Ljava.lang.Class; (java.base@21.0.12.1)
  20:           341          10912  jdk.internal.math.FDBigInteger (java.base@21.0.12.1)
  21:           207           9936  java.lang.invoke.MemberName (java.base@21.0.12.1)
  22:           235           9400  java.lang.invoke.MethodType (java.base@21.0.12.1)
  23:           367           8808  java.lang.module.ModuleDescriptor$Exports (java.base@21.0.12.1)
  24:           489           7824  jdk.internal.util.StrongReferenceKey (java.base@21.0.12.1)
  25:           102           7752  [Ljava.lang.ref.SoftReference; (java.base@21.0.12.1)
  26:            12           6720  [Ljdk.internal.org.objectweb.asm.SymbolTable$Entry; (java.base@21.0.12.1)
  27:           256           6144  java.lang.Long (java.base@21.0.12.1)
  28:           164           5248  java.lang.invoke.LambdaForm$Name (java.base@21.0.12.1)
  29:            62           4960  java.net.URI (java.base@21.0.12.1)
  30:            46           4608  [Ljava.lang.invoke.MethodHandle; (java.base@21.0.12.1)
  31:           137           4384  java.lang.module.ModuleDescriptor$Requires (java.base@21.0.12.1)
  32:           262           4192  java.lang.Integer (java.base@21.0.12.1)
  33:            91           4184  [Ljava.lang.invoke.LambdaForm$Name; (java.base@21.0.12.1)
  34:           257           4112  java.lang.Byte (java.base@21.0.12.1)
  35:           256           4096  java.lang.Short (java.base@21.0.12.1)
  36:           127           4064  jdk.internal.util.WeakReferenceKey (java.base@21.0.12.1)
  37:            62           3968  java.lang.module.ModuleDescriptor (java.base@21.0.12.1)
  38:            68           3808  java.lang.Module (java.base@21.0.12.1)
  39:           145           3480  java.util.ImmutableCollections$SetN (java.base@21.0.12.1)
  40:            62           3472  jdk.internal.module.ModuleReferenceImpl (java.base@21.0.12.1)
  41:           203           3248  java.util.HashSet (java.base@21.0.12.1)
  42:            12           2688  jdk.internal.org.objectweb.asm.MethodWriter (java.base@21.0.12.1)
  43:            39           2496  java.util.concurrent.ConcurrentHashMap (java.base@21.0.12.1)
  44:            68           2176  java.lang.invoke.LambdaForm$Kind (java.base@21.0.12.1)
  45:            86           2064  java.lang.StringBuilder (java.base@21.0.12.1)
  46:           128           2048  java.lang.Character (java.base@21.0.12.1)
  47:            84           2016  java.lang.invoke.ResolvedMethodName (java.base@21.0.12.1)
  48:            61           1952  jdk.internal.loader.BuiltinClassLoader$LoadedModule (java.base@21.0.12.1)
  49:            51           1632  java.lang.invoke.MethodTypeForm (java.base@21.0.12.1)
  50:            67           1608  java.util.ImmutableCollections$List12 (java.base@21.0.12.1)
  51:            32           1536  java.lang.invoke.LambdaForm (java.base@21.0.12.1)
  52:            62           1488  java.lang.module.ModuleDescriptor$Provides (java.base@21.0.12.1)
  53:            62           1488  java.lang.module.ResolvedModule (java.base@21.0.12.1)
  54:            62           1488  jdk.internal.module.ServicesCatalog$ServiceProvider (java.base@21.0.12.1)
  55:            62           1488  jdk.internal.module.SystemModuleFinders$2 (java.base@21.0.12.1)
  56:            25           1400  jdk.internal.org.objectweb.asm.Label (java.base@21.0.12.1)
  57:             1           1376  [Ljdk.internal.math.FDBigInteger; (java.base@21.0.12.1)
  58:             6           1336  [J (java.base@21.0.12.1)
  59:            33           1320  java.lang.ref.SoftReference (java.base@21.0.12.1)
  60:            79           1264  java.lang.Object (java.base@21.0.12.1)
  61:            48           1152  java.lang.invoke.LambdaForm$NamedFunction (java.base@21.0.12.1)
  62:            24           1152  jdk.internal.org.objectweb.asm.Frame (java.base@21.0.12.1)
  63:             3           1096  [Ljava.lang.Integer; (java.base@21.0.12.1)
  64:             9           1080  java.lang.Thread (java.base@21.0.12.1)
  65:             1           1040  [Ljava.lang.Byte; (java.base@21.0.12.1)
  66:             1           1040  [Ljava.lang.Long; (java.base@21.0.12.1)
  67:             1           1040  [Ljava.lang.Short; (java.base@21.0.12.1)
  68:            22           1040  [Ljava.lang.String; (java.base@21.0.12.1)
  69:            42           1008  jdk.internal.org.objectweb.asm.ByteVector (java.base@21.0.12.1)
  70:            31            992  java.lang.invoke.VarHandle$AccessMode (java.base@21.0.12.1)
  71:            60            960  jdk.internal.module.SystemModuleFinders$3 (java.base@21.0.12.1)
  72:             6            912  jdk.internal.org.objectweb.asm.ClassWriter (java.base@21.0.12.1)
  73:            18            864  jdk.internal.org.objectweb.asm.AnnotationWriter (java.base@21.0.12.1)
  74:            33            792  java.util.ArrayList (java.base@21.0.12.1)
  75:            24            768  jdk.internal.org.objectweb.asm.Type (java.base@21.0.12.1)
  76:            19            760  java.lang.invoke.DirectMethodHandle (java.base@21.0.12.1)
  77:            30            720  java.util.concurrent.CopyOnWriteArrayList (java.base@21.0.12.1)
  78:            22            704  java.lang.reflect.ClassFileFormatVersion (java.base@21.0.12.1)
  79:            27            648  java.util.jar.Attributes$Name (java.base@21.0.12.1)
  80:             3            624  [Ljava.lang.invoke.LambdaForm; (java.base@21.0.12.1)
  81:            19            608  sun.util.locale.BaseLocale (java.base@21.0.12.1)
  82:            15            600  java.io.ObjectStreamField (java.base@21.0.12.1)
  83:             2            576  [Ljava.lang.invoke.LambdaForm$Kind; (java.base@21.0.12.1)
  84:            12            576  java.lang.invoke.DirectMethodHandle$Accessor (java.base@21.0.12.1)
  85:            24            576  java.lang.invoke.LambdaFormEditor$TransformKey (java.base@21.0.12.1)
  86:            24            576  java.util.ImmutableCollections$ListN (java.base@21.0.12.1)
  87:             7            560  java.util.concurrent.TimeUnit (java.base@21.0.12.1)
  88:            10            560  sun.invoke.util.Wrapper (java.base@21.0.12.1)
  89:             1            528  [Ljava.lang.Character; (java.base@21.0.12.1)
  90:            13            520  java.security.AccessControlContext (java.base@21.0.12.1)
  91:            12            480  java.lang.OutOfMemoryError (java.base@21.0.12.1)
  92:            12            480  java.lang.Thread$FieldHolder (java.base@21.0.12.1)
  93:            14            448  java.lang.invoke.MethodHandles$Lookup (java.base@21.0.12.1)
  94:             7            416  [Lsun.invoke.util.Wrapper; (java.base@21.0.12.1)
  95:            11            392  [Ljava.lang.invoke.LambdaForm$NamedFunction; (java.base@21.0.12.1)
  96:             8            384  java.lang.invoke.BoundMethodHandle$Species_LLLL (java.base@21.0.12.1)
  97:             6            384  java.lang.invoke.InvokerBytecodeGenerator (java.base@21.0.12.1)
  98:            24            384  java.lang.invoke.LambdaFormEditor (java.base@21.0.12.1)
  99:            12            384  java.util.concurrent.locks.ReentrantLock$NonfairSync (java.base@21.0.12.1)
 100:             6            384  jdk.internal.org.objectweb.asm.FieldWriter (java.base@21.0.12.1)
 101:             6            384  jdk.internal.org.objectweb.asm.SymbolTable (java.base@21.0.12.1)
 102:             5            336  [S (java.base@21.0.12.1)
 103:             8            320  java.lang.invoke.BoundMethodHandle$Species_LL (java.base@21.0.12.1)
 104:             2            288  [Ljava.lang.invoke.VarHandle$AccessMode; (java.base@21.0.12.1)
 105:             6            288  java.lang.invoke.BoundMethodHandle$SpeciesData (java.base@21.0.12.1)
 106:             6            288  java.lang.invoke.LambdaFormBuffer (java.base@21.0.12.1)
 107:             6            288  java.lang.invoke.LambdaFormEditor$Transform (java.base@21.0.12.1)
 108:             9            288  java.util.ArrayList$Itr (java.base@21.0.12.1)
 109:             7            280  java.lang.invoke.BoundMethodHandle$Species_L (java.base@21.0.12.1)
 110:            12            272  [Ljdk.internal.org.objectweb.asm.Type; (java.base@21.0.12.1)
 111:            11            264  java.lang.invoke.MethodHandleImpl$Intrinsic (java.base@21.0.12.1)
 112:             6            240  [Ljava.lang.invoke.BoundMethodHandle$SpeciesData; (java.base@21.0.12.1)
 113:             6            240  java.lang.invoke.LambdaForm$BasicType (java.base@21.0.12.1)
 114:            10            240  java.nio.charset.CoderResult (java.base@21.0.12.1)
 115:             7            224  java.util.Hashtable$Entry (java.base@21.0.12.1)
 116:             5            200  java.security.ProtectionDomain (java.base@21.0.12.1)
 117:             5            200  java.util.WeakHashMap$Entry (java.base@21.0.12.1)
 118:             4            192  java.lang.invoke.BoundMethodHandle$Species_LJ (java.base@21.0.12.1)
 119:             4            192  java.lang.invoke.BoundMethodHandle$Species_LLL (java.base@21.0.12.1)
 120:             8            192  java.lang.invoke.Invokers (java.base@21.0.12.1)
 121:             6            192  java.lang.invoke.MethodHandles$Lookup$ClassDefiner (java.base@21.0.12.1)
 122:             3            192  java.net.URL (java.base@21.0.12.1)
 123:             6            192  java.util.ImmutableCollections$MapN (java.base@21.0.12.1)
 124:             6            192  java.util.concurrent.ConcurrentHashMap$ReservationNode (java.base@21.0.12.1)
 125:            12            192  java.util.concurrent.locks.ReentrantLock (java.base@21.0.12.1)
 126:             7            184  [Ljava.io.ObjectStreamField; (java.base@21.0.12.1)
 127:             2            176  java.lang.reflect.Method (java.base@21.0.12.1)
 128:             4            160  [Ljava.lang.invoke.LambdaForm$BasicType; (java.base@21.0.12.1)
 129:             5            160  java.io.File (java.base@21.0.12.1)
 130:             4            160  java.io.FileDescriptor (java.base@21.0.12.1)
 131:             4            160  java.lang.invoke.ConstantCallSite (java.base@21.0.12.1)
 132:             5            160  java.lang.invoke.VarHandle$AccessType (java.base@21.0.12.1)
 133:             4            160  java.security.CodeSource (java.base@21.0.12.1)
 134:             3            152  [Ljava.lang.invoke.MethodType; (java.base@21.0.12.1)
 135:             6            144  java.lang.Thread$State (java.base@21.0.12.1)
 136:             3            144  java.lang.ThreadGroup (java.base@21.0.12.1)
 137:             6            144  java.lang.invoke.InvokerBytecodeGenerator$ClassData (java.base@21.0.12.1)
 138:             6            144  java.lang.invoke.MethodHandles$Lookup$ClassFile (java.base@21.0.12.1)
 139:             3            144  java.util.Hashtable (java.base@21.0.12.1)
 140:             6            144  java.util.ImmutableCollections$SetN$SetNIterator (java.base@21.0.12.1)
 141:             9            144  jdk.internal.misc.InternalLock (java.base@21.0.12.1)
 142:             2            128  [Ljava.lang.invoke.MethodHandleImpl$Intrinsic; (java.base@21.0.12.1)
 143:             3            128  [Ljava.util.Hashtable$Entry; (java.base@21.0.12.1)
 144:             4            128  java.lang.module.ModuleDescriptor$Requires$Modifier (java.base@21.0.12.1)
 145:             4            128  jdk.internal.loader.NativeLibraries (java.base@21.0.12.1)
 146:             1            120  java.lang.ref.Finalizer$FinalizerThread (java.base@21.0.12.1)
 147:             1            120  java.lang.ref.Reference$ReferenceHandler (java.base@21.0.12.1)
 148:             1            120  jdk.internal.misc.InnocuousThread (java.base@21.0.12.1)
 149:             2            112  java.nio.HeapByteBuffer (java.base@21.0.12.1)
 150:             2            112  sun.nio.cs.StreamEncoder (java.base@21.0.12.1)
 151:             1            104  [Ljava.lang.reflect.ClassFileFormatVersion; (java.base@21.0.12.1)
 152:             1            104  jdk.internal.loader.ClassLoaders$AppClassLoader (java.base@21.0.12.1)
 153:             1            104  jdk.internal.loader.ClassLoaders$BootClassLoader (java.base@21.0.12.1)
 154:             1            104  jdk.internal.loader.ClassLoaders$PlatformClassLoader (java.base@21.0.12.1)
 155:             1             96  [Lsun.util.locale.BaseLocale; (java.base@21.0.12.1)
 156:             2             96  java.io.PrintStream (java.base@21.0.12.1)
 157:             4             96  java.lang.module.ModuleDescriptor$Opens (java.base@21.0.12.1)
 158:             3             96  java.lang.ref.ReferenceQueue (java.base@21.0.12.1)
 159:             3             96  java.util.HexFormat (java.base@21.0.12.1)
 160:             2             96  sun.nio.cs.US_ASCII$Encoder (java.base@21.0.12.1)
 161:             2             80  [Ljava.lang.OutOfMemoryError; (java.base@21.0.12.1)
 162:             5             80  [Ljava.security.Principal; (java.base@21.0.12.1)
 163:             1             80  [Ljava.util.WeakHashMap$Entry; (java.base@21.0.12.1)
 164:             2             80  [[Ljava.lang.invoke.MethodHandle; (java.base@21.0.12.1)
 165:             2             80  java.io.BufferedOutputStream (java.base@21.0.12.1)
 166:             2             80  java.io.BufferedWriter (java.base@21.0.12.1)
 167:             2             80  java.lang.ModuleLayer (java.base@21.0.12.1)
 168:             2             80  java.lang.module.Configuration (java.base@21.0.12.1)
 169:             2             80  java.lang.ref.NativeReferenceQueue (java.base@21.0.12.1)
 170:             5             80  java.security.ProtectionDomain$Key (java.base@21.0.12.1)
 171:             3             72  [Ljava.security.ProtectionDomain; (java.base@21.0.12.1)
 172:             3             72  java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionObject (java.base@21.0.12.1)
 173:             3             72  jdk.internal.misc.Signal (java.base@21.0.12.1)
 174:             2             64  [Ljava.nio.charset.CoderResult; (java.base@21.0.12.1)
 175:             2             64  java.io.FileInputStream (java.base@21.0.12.1)
 176:             2             64  java.io.FileOutputStream (java.base@21.0.12.1)
 177:             1             64  java.lang.Class$ReflectionData (java.base@21.0.12.1)
 178:             2             64  java.lang.invoke.MethodHandles$Lookup$ClassOption (java.base@21.0.12.1)
 179:             2             64  java.lang.ref.ReferenceQueue$Null (java.base@21.0.12.1)
 180:             2             64  jdk.internal.util.ClassFileDumper (java.base@21.0.12.1)
 181:             4             64  jdk.internal.util.Preconditions$4 (java.base@21.0.12.1)
 182:             1             56  java.io.FileCleanable (java.base@21.0.12.1)
 183:             1             56  java.lang.invoke.BoundMethodHandle$Specializer (java.base@21.0.12.1)
 184:             1             56  java.util.Properties (java.base@21.0.12.1)
 185:             1             48  [Ljava.util.concurrent.TimeUnit; (java.base@21.0.12.1)
 186:             1             48  java.io.BufferedInputStream (java.base@21.0.12.1)
 187:             2             48  java.io.File$PathStatus (java.base@21.0.12.1)
 188:             2             48  java.io.OutputStreamWriter (java.base@21.0.12.1)
 189:             1             48  java.lang.NullPointerException (java.base@21.0.12.1)
 190:             1             48  java.lang.invoke.BoundMethodHandle$Specializer$Factory (java.base@21.0.12.1)
 191:             1             48  java.lang.invoke.MethodHandleImpl$AsVarargsCollector (java.base@21.0.12.1)
 192:             3             48  java.nio.charset.CodingErrorAction (java.base@21.0.12.1)
 193:             1             48  java.util.WeakHashMap (java.base@21.0.12.1)
 194:             3             48  java.util.concurrent.atomic.AtomicInteger (java.base@21.0.12.1)
 195:             2             48  jdk.internal.misc.Signal$NativeHandler (java.base@21.0.12.1)
 196:             3             48  jdk.internal.module.ServicesCatalog (java.base@21.0.12.1)
 197:             1             48  jdk.internal.ref.CleanerImpl$PhantomCleanableRef (java.base@21.0.12.1)
 198:             3             48  jdk.internal.reflect.ReflectionFactory$GetReflectionFactoryAction (java.base@21.0.12.1)
 199:             2             48  sun.nio.cs.Surrogate$Parser (java.base@21.0.12.1)
 200:             1             40  [Ljava.lang.Thread$State; (java.base@21.0.12.1)
 201:             1             40  [Ljava.lang.invoke.VarHandle$AccessType; (java.base@21.0.12.1)
 202:             1             40  [[Ljava.lang.invoke.LambdaForm$Name; (java.base@21.0.12.1)
 203:             1             40  java.lang.ArithmeticException (java.base@21.0.12.1)
 204:             1             40  java.lang.VirtualMachineError (java.base@21.0.12.1)
 205:             1             40  jdk.internal.event.ThreadSleepEvent (java.base@21.0.12.1)
 206:             1             40  jdk.internal.loader.URLClassPath (java.base@21.0.12.1)
 207:             1             40  jdk.internal.ref.CleanerImpl$CleanerCleanable (java.base@21.0.12.1)
 208:             1             40  sun.nio.cs.StandardCharsets$Aliases (java.base@21.0.12.1)
 209:             1             40  sun.nio.cs.StandardCharsets$Cache (java.base@21.0.12.1)
 210:             2             32  [Ljava.lang.StackTraceElement; (java.base@21.0.12.1)
 211:             1             32  [Ljava.lang.ThreadGroup; (java.base@21.0.12.1)
 212:             1             32  [Ljava.lang.module.ModuleDescriptor$Requires$Modifier; (java.base@21.0.12.1)
 213:             1             32  [Ljava.lang.ref.WeakReference; (java.base@21.0.12.1)
 214:             1             32  [Ljdk.internal.module.ServicesCatalog; (java.base@21.0.12.1)
 215:             2             32  java.lang.Boolean (java.base@21.0.12.1)
 216:             1             32  java.lang.Module$ArchivedData (java.base@21.0.12.1)
 217:             1             32  java.lang.module.ModuleDescriptor$Version (java.base@21.0.12.1)
 218:             2             32  java.lang.ref.NativeReferenceQueue$Lock (java.base@21.0.12.1)
 219:             1             32  java.lang.ref.WeakReference (java.base@21.0.12.1)
 220:             2             32  java.nio.ByteOrder (java.base@21.0.12.1)
 221:             1             32  java.util.Collections$UnmodifiableMap (java.base@21.0.12.1)
 222:             1             32  java.util.HashMap$EntryIterator (java.base@21.0.12.1)
 223:             1             32  java.util.concurrent.locks.AbstractQueuedSynchronizer$ConditionNode (java.base@21.0.12.1)
 224:             1             32  java.util.concurrent.locks.AbstractQueuedSynchronizer$ExclusiveNode (java.base@21.0.12.1)
 225:             1             32  jdk.internal.loader.ArchivedClassLoaders (java.base@21.0.12.1)
 226:             2             32  jdk.internal.loader.ClassLoaderValue (java.base@21.0.12.1)
 227:             1             32  jdk.internal.loader.URLClassPath$FileLoader (java.base@21.0.12.1)
 228:             1             32  jdk.internal.loader.URLClassPath$FileLoader$1 (java.base@21.0.12.1)
 229:             1             32  jdk.internal.module.ArchivedModuleGraph (java.base@21.0.12.1)
 230:             1             24  [Ljava.io.File$PathStatus; (java.base@21.0.12.1)
 231:             1             24  [Ljava.lang.invoke.MethodHandles$Lookup$ClassOption; (java.base@21.0.12.1)
 232:             1             24  [Ljava.lang.reflect.Method; (java.base@21.0.12.1)
 233:             1             24  [Ljava.net.URL; (java.base@21.0.12.1)
 234:             1             24  [Ljava.util.jar.Manifest; (java.base@21.0.12.1)
 235:             1             24  java.io.UnixFileSystem (java.base@21.0.12.1)
 236:             1             24  java.lang.Double (java.base@21.0.12.1)
 237:             1             24  java.lang.NamedPackage (java.base@21.0.12.1)
 238:             1             24  java.lang.PublicMethods$MethodList (java.base@21.0.12.1)
 239:             1             24  java.lang.RuntimePermission (java.base@21.0.12.1)
 240:             1             24  java.security.BasicPermissionCollection (java.base@21.0.12.1)
 241:             1             24  java.security.Permissions (java.base@21.0.12.1)
 242:             1             24  java.util.ArrayDeque (java.base@21.0.12.1)
 243:             1             24  java.util.Collections$EmptyMap (java.base@21.0.12.1)
 244:             1             24  java.util.Collections$SetFromMap (java.base@21.0.12.1)
 245:             1             24  java.util.ImmutableCollections$MapN$MapNIterator (java.base@21.0.12.1)
 246:             1             24  java.util.concurrent.ConcurrentHashMap$KeySetView (java.base@21.0.12.1)
 247:             1             24  jdk.internal.loader.URLClassPath$3 (java.base@21.0.12.1)
 248:             1             24  jdk.internal.module.ModuleHashes (java.base@21.0.12.1)
 249:             1             24  jdk.internal.module.SystemModuleFinders$SystemModuleFinder (java.base@21.0.12.1)
 250:             1             24  jdk.internal.ref.CleanerImpl (java.base@21.0.12.1)
 251:             1             24  jdk.internal.reflect.ReflectionFactory$Config (java.base@21.0.12.1)
 252:             1             24  jdk.internal.util.ReferencedKeyMap (java.base@21.0.12.1)
 253:             1             24  sun.nio.cs.ISO_8859_1 (java.base@21.0.12.1)
 254:             1             24  sun.nio.cs.StandardCharsets (java.base@21.0.12.1)
 255:             1             24  sun.nio.cs.US_ASCII (java.base@21.0.12.1)
 256:             1             24  sun.nio.cs.UTF_16 (java.base@21.0.12.1)
 257:             1             24  sun.nio.cs.UTF_16BE (java.base@21.0.12.1)
 258:             1             24  sun.nio.cs.UTF_16LE (java.base@21.0.12.1)
 259:             1             24  sun.nio.cs.UTF_8 (java.base@21.0.12.1)
 260:             1             24  sun.security.util.LazyCodeSourcePermissionCollection (java.base@21.0.12.1)
 261:             1             16  [D (java.base@21.0.12.1)
 262:             1             16  [F (java.base@21.0.12.1)
 263:             1             16  [Ljava.lang.Throwable; (java.base@21.0.12.1)
 264:             1             16  [Ljava.security.cert.Certificate; (java.base@21.0.12.1)
 265:             1             16  [Z (java.base@21.0.12.1)
 266:             1             16  java.io.FileDescriptor$1 (java.base@21.0.12.1)
 267:             1             16  java.io.FileInputStream$1 (java.base@21.0.12.1)
 268:             1             16  java.io.PrintStream$1 (java.base@21.0.12.1)
 269:             1             16  java.lang.CharacterDataLatin1 (java.base@21.0.12.1)
 270:             1             16  java.lang.Float (java.base@21.0.12.1)
 271:             1             16  java.lang.Runtime (java.base@21.0.12.1)
 272:             1             16  java.lang.String$CaseInsensitiveComparator (java.base@21.0.12.1)
 273:             1             16  java.lang.System$2 (java.base@21.0.12.1)
 274:             1             16  java.lang.Terminator$1 (java.base@21.0.12.1)
 275:             1             16  java.lang.invoke.ClassSpecializer$1 (java.base@21.0.12.1)
 276:             1             16  java.lang.invoke.MemberName$Factory (java.base@21.0.12.1)
 277:             1             16  java.lang.invoke.MethodHandleImpl$1 (java.base@21.0.12.1)
 278:             1             16  java.lang.invoke.MethodType$1 (java.base@21.0.12.1)
 279:             1             16  java.lang.module.ModuleDescriptor$1 (java.base@21.0.12.1)
 280:             1             16  java.lang.ref.Cleaner (java.base@21.0.12.1)
 281:             1             16  java.lang.ref.Cleaner$1 (java.base@21.0.12.1)
 282:             1             16  java.lang.ref.Reference$1 (java.base@21.0.12.1)
 283:             1             16  java.lang.reflect.ReflectAccess (java.base@21.0.12.1)
 284:             1             16  java.net.URI$1 (java.base@21.0.12.1)
 285:             1             16  java.net.URL$3 (java.base@21.0.12.1)
 286:             1             16  java.net.URL$DefaultFactory (java.base@21.0.12.1)
 287:             1             16  java.nio.Buffer$1 (java.base@21.0.12.1)
 288:             1             16  java.nio.Buffer$2 (java.base@21.0.12.1)
 289:             1             16  java.security.ProtectionDomain$JavaSecurityAccessImpl (java.base@21.0.12.1)
 290:             1             16  java.security.SecureClassLoader$1 (java.base@21.0.12.1)
 291:             1             16  java.security.SecureClassLoader$CodeSourceKey (java.base@21.0.12.1)
 292:             1             16  java.util.Collections$EmptyList (java.base@21.0.12.1)
 293:             1             16  java.util.Collections$EmptySet (java.base@21.0.12.1)
 294:             1             16  java.util.HashMap$EntrySet (java.base@21.0.12.1)
 295:             1             16  java.util.ImmutableCollections$MapN$1 (java.base@21.0.12.1)
 296:             1             16  java.util.WeakHashMap$KeySet (java.base@21.0.12.1)
 297:             1             16  jdk.internal.misc.ScopedMemoryAccess (java.base@21.0.12.1)
 298:             1             16  jdk.internal.misc.Unsafe (java.base@21.0.12.1)
 299:             1             16  jdk.internal.module.ArchivedBootLayer (java.base@21.0.12.1)
 300:             1             16  jdk.internal.module.ModuleLoaderMap$Mapper (java.base@21.0.12.1)
 301:             1             16  jdk.internal.module.ModulePatcher (java.base@21.0.12.1)
 302:             1             16  jdk.internal.module.ModuleTarget (java.base@21.0.12.1)
 303:             1             16  jdk.internal.ref.CleanerFactory$1 (java.base@21.0.12.1)
 304:             1             16  jdk.internal.reflect.ReflectionFactory (java.base@21.0.12.1)
 305:             1             16  jdk.internal.util.Preconditions$1 (java.base@21.0.12.1)
 306:             1             16  jdk.internal.util.Preconditions$2 (java.base@21.0.12.1)
 307:             1             16  jdk.internal.util.Preconditions$3 (java.base@21.0.12.1)
 308:             1             16  jdk.internal.util.ReferencedKeySet (java.base@21.0.12.1)
 309:             1             16  jdk.internal.util.SystemProps$Raw (java.base@21.0.12.1)
 310:             1             16  sun.net.www.protocol.file.Handler (java.base@21.0.12.1)
 311:             1             16  sun.net.www.protocol.jar.Handler (java.base@21.0.12.1)
Total        390948       14227744
"""

# real: node --cpu-prof --cpu-prof-interval 1000 on a 0.2 s script; path shortened
CPUPROFILE = """\
{"nodes":[{"id":1,"callFrame":{"functionName":"(root)","scriptId":"0","url":"","lineNumber":-1,"columnNumber":-1},"hitCount":0,"children":[2,3,25]},{"id":2,"callFrame":{"functionName":"(program)","scriptId":"0","url":"","lineNumber":-1,"columnNumber":-1},"hitCount":2},{"id":3,"callFrame":{"functionName":"","scriptId":"77","url":"node:internal/main/run_main_module","lineNumber":0,"columnNumber":0},"hitCount":0,"children":[4]},{"id":4,"callFrame":{"functionName":"executeUserEntryPoint","scriptId":"73","url":"node:internal/modules/run_main","lineNumber":156,"columnNumber":30},"hitCount":0,"children":[5]},{"id":5,"callFrame":{"functionName":"wrapModuleLoad","scriptId":"67","url":"node:internal/modules/cjs/loader","lineNumber":236,"columnNumber":23},"hitCount":0,"children":[6]},{"id":6,"callFrame":{"functionName":"","scriptId":"67","url":"node:internal/modules/cjs/loader","lineNumber":1192,"columnNumber":23},"hitCount":0,"children":[7]},{"id":7,"callFrame":{"functionName":"","scriptId":"67","url":"node:internal/modules/cjs/loader","lineNumber":1489,"columnNumber":32},"hitCount":0,"children":[8]},{"id":8,"callFrame":{"functionName":"","scriptId":"67","url":"node:internal/modules/cjs/loader","lineNumber":1877,"columnNumber":36},"hitCount":0,"children":[9]},{"id":9,"callFrame":{"functionName":"","scriptId":"67","url":"node:internal/modules/cjs/loader","lineNumber":1730,"columnNumber":36},"hitCount":0,"children":[10]},{"id":10,"callFrame":{"functionName":"","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":0},"hitCount":0,"children":[11,33]},{"id":11,"callFrame":{"functionName":"work","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":3,"columnNumber":13},"hitCount":5,"children":[12,13,14],"positionTicks":[{"line":6,"ticks":5}]},{"id":12,"callFrame":{"functionName":"build","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":2,"columnNumber":14},"hitCount":41,"positionTicks":[{"line":3,"ticks":41}]},{"id":13,"callFrame":{"functionName":"parse","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":1,"columnNumber":14},"hitCount":98,"positionTicks":[{"line":2,"ticks":98}]},{"id":14,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":0,"children":[15]},{"id":15,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":0,"children":[16]},{"id":16,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":0,"children":[17]},{"id":17,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":1,"children":[18],"positionTicks":[{"line":1,"ticks":1}]},{"id":18,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":0,"children":[19]},{"id":19,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":1,"children":[20],"positionTicks":[{"line":1,"ticks":1}]},{"id":20,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":0,"children":[21]},{"id":21,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":0,"children":[22]},{"id":22,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":1,"children":[23],"positionTicks":[{"line":1,"ticks":1}]},{"id":23,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":2,"children":[24],"positionTicks":[{"line":1,"ticks":2}]},{"id":24,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":3,"children":[26],"positionTicks":[{"line":1,"ticks":3}]},{"id":26,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":3,"children":[27],"positionTicks":[{"line":1,"ticks":3}]},{"id":27,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":4,"children":[28],"positionTicks":[{"line":1,"ticks":4}]},{"id":28,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":2,"children":[29],"positionTicks":[{"line":1,"ticks":2}]},{"id":29,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":1,"children":[30],"positionTicks":[{"line":1,"ticks":1}]},{"id":30,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":2,"children":[31],"positionTicks":[{"line":1,"ticks":2}]},{"id":31,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":3,"children":[32],"positionTicks":[{"line":1,"ticks":3}]},{"id":32,"callFrame":{"functionName":"fib","scriptId":"78","url":"file:///srv/app/hot.js","lineNumber":0,"columnNumber":12},"hitCount":2,"positionTicks":[{"line":1,"ticks":2}]},{"id":33,"callFrame":{"functionName":"consoleCall","scriptId":"0","url":"","lineNumber":-1,"columnNumber":-1},"hitCount":0,"children":[34]},{"id":34,"callFrame":{"functionName":"log","scriptId":"58","url":"node:internal/console/constructor","lineNumber":400,"columnNumber":5},"hitCount":0,"children":[35,59]},{"id":35,"callFrame":{"functionName":"value","scriptId":"58","url":"node:internal/console/constructor","lineNumber":337,"columnNumber":19},"hitCount":0,"children":[36]},{"id":36,"callFrame":{"functionName":"get","scriptId":"58","url":"node:internal/console/constructor","lineNumber":209,"columnNumber":13},"hitCount":0,"children":[37]},{"id":37,"callFrame":{"functionName":"getStdout","scriptId":"63","url":"node:internal/bootstrap/switches/is_main_thread","lineNumber":150,"columnNumber":18},"hitCount":0,"children":[38]},{"id":38,"callFrame":{"functionName":"createWritableStdioStream","scriptId":"63","url":"node:internal/bootstrap/switches/is_main_thread","lineNumber":49,"columnNumber":34},"hitCount":0,"children":[39]},{"id":39,"callFrame":{"functionName":"requireBuiltin","scriptId":"6","url":"node:internal/bootstrap/realm","lineNumber":421,"columnNumber":23},"hitCount":0,"children":[40]},{"id":40,"callFrame":{"functionName":"compileForInternalLoader","scriptId":"6","url":"node:internal/bootstrap/realm","lineNumber":384,"columnNumber":26},"hitCount":0,"children":[41]},{"id":41,"callFrame":{"functionName":"","scriptId":"79","url":"node:internal/fs/sync_write_stream","lineNumber":0,"columnNumber":0},"hitCount":1,"children":[42],"positionTicks":[{"line":23,"ticks":1}]},{"id":42,"callFrame":{"functionName":"requireBuiltin","scriptId":"6","url":"node:internal/bootstrap/realm","lineNumber":421,"columnNumber":23},"hitCount":0,"children":[43]},{"id":43,"callFrame":{"functionName":"compileForInternalLoader","scriptId":"6","url":"node:internal/bootstrap/realm","lineNumber":384,"columnNumber":26},"hitCount":1,"children":[44],"positionTicks":[{"line":397,"ticks":1}]},{"id":44,"callFrame":{"functionName":"","scriptId":"80","url":"node:stream","lineNumber":0,"columnNumber":0},"hitCount":0,"children":[45]},{"id":45,"callFrame":{"functionName":"requireBuiltin","scriptId":"6","url":"node:internal/bootstrap/realm","lineNumber":421,"columnNumber":23},"hitCount":0,"children":[46]},{"id":46,"callFrame":{"functionName":"compileForInternalLoader","scriptId":"6","url":"node:internal/bootstrap/realm","lineNumber":384,"columnNumber":26},"hitCount":0,"children":[47]},{"id":47,"callFrame":{"functionName":"","scriptId":"81","url":"node:internal/streams/operators","lineNumber":0,"columnNumber":0},"hitCount":0,"children":[48]},{"id":48,"callFrame":{"functionName":"requireBuiltin","scriptId":"6","url":"node:internal/bootstrap/realm","lineNumber":421,"columnNumber":23},"hitCount":0,"children":[49]},{"id":49,"callFrame":{"functionName":"compileForInternalLoader","scriptId":"6","url":"node:internal/bootstrap/realm","lineNumber":384,"columnNumber":26},"hitCount":0,"children":[50]},{"id":50,"callFrame":{"functionName":"","scriptId":"84","url":"node:internal/streams/compose","lineNumber":0,"columnNumber":0},"hitCount":0,"children":[51]},{"id":51,"callFrame":{"functionName":"requireBuiltin","scriptId":"6","url":"node:internal/bootstrap/realm","lineNumber":421,"columnNumber":23},"hitCount":0,"children":[52]},{"id":52,"callFrame":{"functionName":"compileForInternalLoader","scriptId":"6","url":"node:internal/bootstrap/realm","lineNumber":384,"columnNumber":26},"hitCount":0,"children":[53]},{"id":53,"callFrame":{"functionName":"","scriptId":"85","url":"node:internal/streams/pipeline","lineNumber":0,"columnNumber":0},"hitCount":0,"children":[54]},{"id":54,"callFrame":{"functionName":"requireBuiltin","scriptId":"6","url":"node:internal/bootstrap/realm","lineNumber":421,"columnNumber":23},"hitCount":0,"children":[55]},{"id":55,"callFrame":{"functionName":"compileForInternalLoader","scriptId":"6","url":"node:internal/bootstrap/realm","lineNumber":384,"columnNumber":26},"hitCount":0,"children":[56]},{"id":56,"callFrame":{"functionName":"","scriptId":"87","url":"node:internal/streams/duplex","lineNumber":0,"columnNumber":0},"hitCount":0,"children":[57]},{"id":57,"callFrame":{"functionName":"requireBuiltin","scriptId":"6","url":"node:internal/bootstrap/realm","lineNumber":421,"columnNumber":23},"hitCount":0,"children":[58]},{"id":58,"callFrame":{"functionName":"compileForInternalLoader","scriptId":"6","url":"node:internal/bootstrap/realm","lineNumber":384,"columnNumber":26},"hitCount":2,"positionTicks":[{"line":397,"ticks":2}]},{"id":59,"callFrame":{"functionName":"value","scriptId":"58","url":"node:internal/console/constructor","lineNumber":274,"columnNumber":19},"hitCount":0,"children":[60]},{"id":60,"callFrame":{"functionName":"Writable.write","scriptId":"94","url":"node:internal/streams/writable","lineNumber":503,"columnNumber":35},"hitCount":0,"children":[61]},{"id":61,"callFrame":{"functionName":"_write","scriptId":"94","url":"node:internal/streams/writable","lineNumber":452,"columnNumber":15},"hitCount":1,"positionTicks":[{"line":501,"ticks":1}]},{"id":25,"callFrame":{"functionName":"(garbage collector)","scriptId":"0","url":"","lineNumber":-1,"columnNumber":-1},"hitCount":10}],"startTime":1226385229,"endTime":1226611893,"samples":[2,12,13,22,24,12,13,25,13,25,31,13,13,12,24,13,13,11,17,13,13,25,13,13,13,25,13,13,26,13,13,12,13,12,13,12,13,25,13,12,13,27,13,27,13,13,13,32,13,31,13,13,12,13,12,13,12,13,12,13,11,13,11,13,13,13,13,12,13,12,13,12,13,12,13,32,13,13,12,13,12,13,12,13,13,13,13,31,13,13,12,13,12,13,12,13,12,13,12,13,25,13,28,13,27,13,12,13,12,13,12,13,25,25,13,26,13,11,13,12,13,13,13,13,13,12,13,23,13,12,13,11,13,27,13,13,12,13,12,13,12,30,13,13,13,12,30,13,13,12,12,19,13,13,25,12,24,13,13,13,13,12,13,13,13,12,12,29,13,12,13,23,13,13,13,12,26,28,25,13,43,58,58,41,61],"timeDeltas":[11742,2401,1082,1152,1102,1287,1114,1107,1199,1089,1120,1085,1135,1161,1090,1090,1121,1180,1132,1128,1156,1120,1128,1239,1147,1087,1890,2051,1123,1146,1087,1093,1072,1082,1138,1166,1106,1136,1166,1219,1037,1091,1116,1089,1140,1113,1160,1179,1174,1047,1102,1109,1084,1120,1123,1112,1107,1109,1093,1093,1102,1114,1124,1097,1148,1135,1103,1126,1122,1157,1158,1090,1131,1115,1082,1097,1094,1080,1110,1098,1197,1111,1162,1104,1802,1133,1168,1321,1154,1124,1114,1150,1092,1101,1092,1092,1104,1086,1159,1142,1141,1136,1169,1113,1108,1129,1158,1126,1114,1166,1101,1181,1142,1133,1139,1098,1144,1091,1100,1148,1147,2398,1106,1239,1134,1187,1041,1127,1093,1150,1156,1120,1120,1116,1151,1140,1136,1123,1148,1122,1138,1168,1130,1159,1126,1183,1172,1164,1153,1151,1168,1162,1174,1228,1217,1089,1160,1178,1182,1201,1149,1108,1147,1178,1188,1193,1145,1120,1141,1176,1148,1154,1158,1138,1163,1142,1206,1138,1151,1109,1162,1171,1140,1175,1191]}"""

# real: lsof -K -a -p PID of node 22 (main-process rows only; the per-thread repeats are rebuilt from the observed pattern)
LSOF_MAIN = """\
COMMAND  PID  TID TASKCMD   USER   FD      TYPE DEVICE  SIZE/OFF   NODE NAME
node    5791                root  cwd       DIR  254,0      4096 712833 /srv/app
node    5791                root  rtd       DIR  254,0      4096      2 /
node    5791                root  txt       REG  254,0 124902584 156675 /opt/node22/bin/node
node    5791                root  mem       REG  254,0   2129424 209637 /usr/lib/x86_64-linux-gnu/libc.so.6
node    5791                root  mem       REG   0,16            13910 anon_inode:[io_uring] (stat: No such file or directory)
node    5791                root  mem       REG   0,16            14579 anon_inode:[io_uring] (stat: No such file or directory)
node    5791                root  mem       REG   0,16            15769 anon_inode:[io_uring] (stat: No such file or directory)
node    5791                root  mem       REG   0,16            14577 anon_inode:[io_uring] (stat: No such file or directory)
node    5791                root  mem       REG   0,16            15761 anon_inode:[io_uring] (stat: No such file or directory)
node    5791                root  mem       REG   0,16            13310 anon_inode:[io_uring] (stat: No such file or directory)
node    5791                root  mem       REG  254,0     14408 209716 /usr/lib/x86_64-linux-gnu/libpthread.so.0
node    5791                root  mem       REG  254,0    952616 209679 /usr/lib/x86_64-linux-gnu/libm.so.6
node    5791                root  mem       REG  254,0   2592224 209730 /usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.33
node    5791                root  mem       REG  254,0    183024 209662 /usr/lib/x86_64-linux-gnu/libgcc_s.so.1
node    5791                root  mem       REG  254,0     14408 209651 /usr/lib/x86_64-linux-gnu/libdl.so.2
node    5791                root  mem       REG   0,16            15758 anon_inode:[io_uring] (stat: No such file or directory)
node    5791                root  mem       REG  254,0    236616 209617 /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2
node    5791                root    0r      CHR    1,3       0t0      3 /dev/null
node    5791                root    1w      REG  254,0       257 682073 /srv/app/out.log
node    5791                root    2w      REG  254,0       257 682073 /srv/app/out.log
node    5791                root    3u  a_inode   0,16         0   2054 [eventpoll]
node    5791                root    4u  a_inode   0,16         0  15758 [io_uring]
node    5791                root    5r     FIFO   0,15       0t0  15759 pipe
node    5791                root    6w     FIFO   0,15       0t0  15759 pipe
node    5791                root    7r     FIFO   0,15       0t0  15760 pipe
node    5791                root    8w     FIFO   0,15       0t0  15760 pipe
node    5791                root    9u  a_inode   0,16         0   2054 [eventfd:6]
node    5791                root   10u  a_inode   0,16         0   2054 [eventpoll:12,14]
node    5791                root   11u  a_inode   0,16         0  13310 [io_uring]
node    5791                root   12r     FIFO   0,15       0t0  13311 pipe
node    5791                root   13w     FIFO   0,15       0t0  13311 pipe
node    5791                root   14u  a_inode   0,16         0   2054 [eventfd:7]
node    5791                root   15u  a_inode   0,16         0   2054 [eventpoll:17,19,44]
node    5791                root   16u  a_inode   0,16         0  15761 [io_uring]
node    5791                root   17r     FIFO   0,15       0t0  15762 pipe
node    5791                root   18w     FIFO   0,15       0t0  15762 pipe
node    5791                root   19u  a_inode   0,16         0   2054 [eventfd:8]
node    5791                root   20r      REG  254,0       406 712834 /srv/app/srv.js
node    5791                root   21r      REG  254,0       406 712834 /srv/app/srv.js
node    5791                root   22r      REG  254,0       406 712834 /srv/app/srv.js
node    5791                root   23u  a_inode   0,16         0   2054 [eventpoll:25,27]
node    5791                root   24u  a_inode   0,16         0  14577 [io_uring]
node    5791                root   25r     FIFO   0,15       0t0  14578 pipe
node    5791                root   26w     FIFO   0,15       0t0  14578 pipe
node    5791                root   27u  a_inode   0,16         0   2054 [eventfd:9]
node    5791                root   28u  a_inode   0,16         0   2054 [eventpoll:30,32]
node    5791                root   29u  a_inode   0,16         0  15769 [io_uring]
node    5791                root   30r     FIFO   0,15       0t0  15770 pipe
node    5791                root   31w     FIFO   0,15       0t0  15770 pipe
node    5791                root   32u  a_inode   0,16         0   2054 [eventfd:10]
node    5791                root   33u  a_inode   0,16         0   2054 [eventpoll:35,37]
node    5791                root   34u  a_inode   0,16         0  14579 [io_uring]
node    5791                root   35r     FIFO   0,15       0t0  14580 pipe
node    5791                root   36w     FIFO   0,15       0t0  14580 pipe
node    5791                root   37u  a_inode   0,16         0   2054 [eventfd:11]
node    5791                root   38u  a_inode   0,16         0   2054 [eventpoll:40,42]
node    5791                root   39u  a_inode   0,16         0  13910 [io_uring]
node    5791                root   40r     FIFO   0,15       0t0  13911 pipe
node    5791                root   41w     FIFO   0,15       0t0  13911 pipe
node    5791                root   42u  a_inode   0,16         0   2054 [eventfd:12]
node    5791                root   43r      CHR    1,3       0t0      3 /dev/null
node    5791                root   44u     IPv4  16388       0t0    TCP localhost:8089 (LISTEN)
"""

LSOF_THREADS = [('5794', 'DelayedTa'), ('5795', 'node'), ('5796', 'node'), ('5797', 'node'), ('5798', 'node'), ('5801', 'node'), ('5802', 'node'), ('5803', 'node'), ('5804', 'node'), ('5805', 'node')]  # real TID, TASKCMD pairs from the same capture
# real: qrcode.print_ascii(invert=True) of exp://192.168.1.23:8081; pyfiglet standard/slant/big/banner3; braille from a sine pattern
ART_QR = """\
█▀▀▀▀▀▀▀█▀██▀▀█▀█▀█▀▀▀▀▀▀▀█
█ █▀▀▀█ █▄▄ ▀ ▀▄▀▀█ █▀▀▀█ █
█ █   █ █▀▀ ▀█ ▀▄██ █   █ █
█ ▀▀▀▀▀ █ █▀▄▀█▀█ █ ▀▀▀▀▀ █
█▀▀▀██▀▀█ ▀ ▄▄▀▀██▀▀▀▀██▀▀█
█▄▄ ▀ ▄▀▀█▄▄▀█▄███  █▄▀▀▄ █
█▄█ █▀▄▀▄  █▀▀ █▀  █▄ ▀█▀██
█▀ ▀▀██▀▀  ██ ▀▀▀█ ▀ █▀▀▄ █
█▀▀▄ ▄ ▀ ▀██▄▄▀ █▀ ▀ ▀▀▄▄▀█
█▀▀▀▀▀▀▀█▄ ▀▀█▄▄  █▀█ ▄██ █
█ █▀▀▀█ █▄▀█▀▀ █▄ ▀▀▀  ▄  █
█ █   █ █▀███ ▀▀▄  █▀▀▄█▀▀█
█ ▀▀▀▀▀ █   ▄▄▀ ██ ▄▀ ▄▄█▀█
▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
"""
ART_FIG_STD = """\
 ____             _                ___  _  __
|  _ \\  ___ _ __ | | ___  _   _   / _ \\| |/ /
| | | |/ _ \\ '_ \\| |/ _ \\| | | | | | | | ' / 
| |_| |  __/ |_) | | (_) | |_| | | |_| | . \\ 
|____/ \\___| .__/|_|\\___/ \\__, |  \\___/|_|\\_\\
           |_|            |___/              
"""
ART_FIG_SLANT = """\
    ____                 
   / __ \\____  ____  ___ 
  / / / / __ \\/ __ \\/ _ \\
 / /_/ / /_/ / / / /  __/
/_____/\\____/_/ /_/\\___/ 
                         
"""
ART_FIG_BIG = """\
                       
                       
 _ __  _ __  _ __ ___  
| '_ \\| '_ \\| '_ ` _ \\ 
| | | | |_) | | | | | |
|_| |_| .__/|_| |_| |_|
      | |              
      |_|              
"""
ART_FIG_BANNER = """\
########  ##     ## #### ##       ########  
##     ## ##     ##  ##  ##       ##     ## 
##     ## ##     ##  ##  ##       ##     ## 
########  ##     ##  ##  ##       ##     ## 
##     ## ##     ##  ##  ##       ##     ## 
##     ## ##     ##  ##  ##       ##     ## 
########   #######  #### ######## ########  
"""
ART_BRAILLE = """\
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠛⠀⠀⠀⠀⠀⠻⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠋⠀⠀⠀⠀⠈⠻⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠿⠃⠀⠀⠀⠀⠘⠻⣿⣿⣿⣿
⠈⠙⠻⠿⢿⡿⠿⠟⠋⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠙⠻⠿⢿⠿⠿⠛⠋⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠛⠻⠿⢿⠿⠿⠛⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠛⠿
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⢀⣠⣴⣶⣿⣿⣶⣦⣄⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣤⣴⣶⣿⣷⣶⣦⣄⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣤⣶⣾⣿⣷⣶⣤⣄⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣤⣶
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⣦⠀⠀⠀⠀⠀⣴⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⣄⠀⠀⠀⠀⢀⣴⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣶⡄⠀⠀⠀⠀⢠⣶⣿⣿⣿⣿
⢿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠟⠁⠀⠀⠀⠀⠀⠙⠻⢿⣿⣿⣿⣿⣿⣿⣿⣿⠿⠛⠁⠀⠀⠀⠀⠀⠙⠻⢿⣿⣿⣿⣿⣿⣿⣿⣿⠿⠛⠁⠀⠀⠀⠀⠀⠙⠿⣿⣿⣿
"""
# ===============================================================================================================
# #219 Fold threads with identical stacks in jstack, Go goroutine and gdb dumps.
# A thread is a paragraph (blank-line separated) whose first line is a jstack, goroutine or gdb header. Its key is the
# header state plus the body with hex literals masked (lock ids on jstack monitor lines are kept: they say WHICH lock).
# Threads with equal keys become one paragraph: 'xN threads "state": names' followed by the first member's own body.

_HEX = re.compile(r"0x[0-9a-fA-F]+")
_J_HDR = re.compile(r'^"(?P<name>.+)" (?P<rest>.*\btid=0x[0-9a-fA-F]+ nid=(?P<nid>\S+)(?P<tail>.*))$')
_GO_HDR = re.compile(r"^goroutine (?P<id>\d+) \[(?P<state>[^\]]*)\]:$")
_GDB_HDR = re.compile(r'^Thread (?P<n>\d+) \(Thread 0x[0-9a-fA-F]+ \(LWP \d+\)(?: "(?P<name>[^"]*)")?\):$')
_MON = re.compile(r"^\s+- (?:waiting to lock|locked|parking to wait for|waiting on|waiting to re-lock in wait\(\)|eliminated) <0x")
_GROUP = re.compile(r"^x(\d+) threads (\"(?:[^\"\\]|\\.)*\"): ")
_BRACKET_HEX = re.compile(r"\s*\[0x[0-9a-fA-F]+\]\s*$")


def _thread_header(line: str):
    """-> (name, state) for a thread header line, else None."""
    if m := _J_HDR.match(line):
        return m.group("name"), _BRACKET_HEX.sub("", m.group("tail")).strip()
    if m := _GO_HDR.match(line):
        return m.group("id"), m.group("state")
    if m := _GDB_HDR.match(line):
        return (m.group("name") if m.group("name") is not None else "#" + m.group("n")), ""
    return None


def _norm_body(lines: list[str]) -> tuple[str, ...]:
    return tuple(l.rstrip() if _MON.match(l) else _HEX.sub("0x", l.rstrip()) for l in lines)


def _fmt_names(names: list[str]) -> str:
    out: list[str] = []
    i = 0
    while i < len(names):
        n = names[i]
        if n.isascii() and n.isdigit() and str(int(n)) == n:
            j = i
            while j + 1 < len(names) and names[j + 1].isascii() and names[j + 1].isdigit() and str(int(names[j + 1])) == names[j + 1] and int(names[j + 1]) == int(names[j]) + 1:
                j += 1
            out.append(n if j == i else f"{n}-{names[j]}")
            i = j + 1
        else:
            out.append(json.dumps(n, ensure_ascii=False))
            i += 1
    return ", ".join(out)


def _parse_names(s: str) -> list[str]:
    dec, out, i = json.JSONDecoder(), [], 0
    while i < len(s):
        if s[i] == '"':
            v, i = dec.raw_decode(s, i)
            out.append(v)
        else:
            m = re.compile(r"(\d+)(?:-(\d+))?").match(s, i)
            if not m:
                raise ValueError("bad name list")
            a, b = int(m.group(1)), int(m.group(2) or m.group(1))
            out += [str(k) for k in range(a, b + 1)]
            i = m.end()
        if s.startswith(", ", i):
            i += 2
        elif i < len(s):
            raise ValueError("bad separator")
    return out


def _paragraphs(text: str):
    """[(is_blank_run, lines)] covering every line of text.split('\\n')."""
    return [(blank, list(g)) for blank, g in itertools.groupby(text.split("\n"), key=lambda l: not l.strip())]


def fold_threads(text: str) -> str:
    paras = _paragraphs(text)
    if any(not blank and _GROUP.match(lines[0]) for blank, lines in paras):
        return text  # looks like our own output already: refuse
    members: dict[tuple, list[int]] = defaultdict(list)
    info: dict[int, tuple] = {}
    for i, (blank, lines) in enumerate(paras):
        if blank:
            continue
        h = _thread_header(lines[0])
        if h is None or '"' in h[1] or "\n" in h[1]:
            continue
        key = (h[1], _norm_body(lines[1:]))
        members[key].append(i)
        info[i] = (h[0], key)
    drop: set[int] = set()
    repl: dict[int, list[str]] = {}
    for key, idxs in members.items():
        if len(idxs) < 2:
            continue
        names = [info[i][0] for i in idxs]
        first = idxs[0]
        repl[first] = [f"x{len(idxs)} threads {json.dumps(key[0])}: {_fmt_names(names)}"] + paras[first][1][1:]
        drop.update(idxs[1:])
    if not repl:
        return text
    segs: list[tuple[bool, list[str]]] = []
    i = 0
    while i < len(paras):
        blank, lines = paras[i]
        if not blank and i in drop:
            if i + 1 < len(paras) and paras[i + 1][0]:
                i += 2  # the blank run that followed a removed thread goes with it
            else:
                i += 1
                if segs and segs[-1][0]:
                    segs.pop()  # last paragraph: drop the separator before it instead
            continue
        segs.append((blank, repl.get(i, lines)))
        i += 1
    out = [l for _, lines in segs for l in lines]
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def probes_219(text: str) -> dict:
    """Parser view: paragraphs -> per-thread (name, state, digest of the masked body), plus every non-thread paragraph verbatim."""
    threads: Counter = Counter()
    other: list[tuple[str, ...]] = []
    blocked: list[tuple] = []
    for blank, lines in _paragraphs(text):
        if blank:
            continue
        g = _GROUP.match(lines[0])
        if g:
            n, state = int(g.group(1)), json.loads(g.group(2))
            names = _parse_names(lines[0][g.end():])
            if len(names) != n:
                other.append(tuple(lines))
                continue
            members = [(nm, state) for nm in names]
            body = lines[1:]
        elif (h := _thread_header(lines[0])) is not None:
            members, body = [(h[0], h[1])], lines[1:]
        else:
            other.append(tuple(l.rstrip() for l in lines))
            continue
        nb = _norm_body(body)
        digest = hashlib.sha1("\n".join(nb).encode()).hexdigest()[:8]
        hot = any(w in (members[0][1] + "\n".join(nb)).lower() for w in ("blocked", "waiting to lock", "mutex", "lock_wait", "semacquire"))
        for nm, st in members:
            threads[(nm, st, digest)] += 1
            if hot:
                blocked.append((nm, st, nb))
    stacks = Counter()
    for (_, _, d), c in threads.items():
        stacks[d] += c
    return {
        "threads (name, state, stack digest)": sorted(threads.items()),
        "threads per distinct stack": sorted(stacks.values(), reverse=True),
        "blocked or lock-waiting threads with their stacks": sorted(blocked),
        "non-thread paragraphs (deadlock report, headers)": other,
    }


def _thread_paras(text: str):
    return [lines for blank, lines in _paragraphs(text) if not blank]


def _reorder(text: str, first_pred, last_pred=None) -> str:
    """Real dump with thread paragraphs moved so a chosen thread comes first (or last): the interesting stack must survive anywhere."""
    paras = _paragraphs(text)
    idx = [i for i, (b, l) in enumerate(paras) if not b and _thread_header(l[0]) is not None]
    pick = [i for i in idx if (first_pred or last_pred)(paras[i][1][0])]
    rest = [i for i in idx if i not in pick]
    seq = (pick + rest) if first_pred else (rest + pick)
    blocks = [paras[i][1] for i in seq]
    out, k = [], 0
    for i, (b, l) in enumerate(paras):
        if i in idx:
            out.append("\n".join(blocks[k]))
            k += 1
        else:
            out.append("\n".join(l))
    return "\n".join(out)


_GO_PANIC = (
    "panic: send on closed channel\n\ngoroutine 1 [running]:\nmain.main()\n\t/srv/orders/main.go:51 +0x1d\nexit status 2\n\n"
    + GOSTACK.split("\n\n", 1)[1]
)
_THREE_SAME_GO = "\n\n".join(f"goroutine {n} [select, 7 minutes]:\nmain.poller(0xc000{n:03x}0, 0x{n:x})\n\t/srv/orders/poll.go:88 +0x{n * 5:x}\ncreated by main.start in goroutine 1\n\t/srv/orders/main.go:31 +0x4c" for n in (7, 8, 9)) + "\n"

case(
    219,
    claim="Threads (jstack, Go goroutine or gdb paragraphs) whose header state and hex-masked stack are identical become one 'xN threads \"state\": names' paragraph with one copy of the stack; "
          "every thread name keeps its state and stack, blocked and lock-waiting stacks keep their lock ids, and non-thread paragraphs (deadlock report) are untouched.",
    loses="jstack thread numbers, tid/nid, cpu and elapsed times, daemon and priority flags; gdb thread numbers and LWPs; the original dump order; hex addresses and arguments in all but the first member of a folded group",
    encode=fold_threads,
    probes=probes_219,
    fixtures=[
        Fixture("jstack: 15 idle pool threads, JVM threads, a deadlock", JSTACK, True),
        Fixture("go deadlock dump: 16 idle workers + 3 mutex waiters + 1 holder", GOSTACK, True),
        Fixture("gdb thread apply all bt: 8 cond-wait workers, 3 mutex waiters", GDBSTACK, True),
        Fixture("jstack, deadlocked thread first", _reorder(JSTACK, lambda h: h.startswith('"order-worker-1"')), True),
        Fixture("jstack, deadlocked threads last", _reorder(JSTACK, None, lambda h: h.startswith('"order-worker-')), True),
        Fixture("go panic traceback then the goroutines", _GO_PANIC, True),
        Fixture("three identical go pollers", _THREE_SAME_GO, True),
        Fixture("one thread only (kill -3 of a small tool)", "Full thread dump OpenJDK 64-Bit Server VM:\n\n\"main\" #1 [10] prio=5 os_prio=0 tid=0x00007f nid=10 runnable  [0x00007f]\n   java.lang.Thread.State: RUNNABLE\n\tat App.main(App.java:3)\n", False),
        Fixture("all stacks distinct", "goroutine 1 [running]:\nmain.main()\n\t/a.go:1 +0x1\n\ngoroutine 2 [chan receive]:\nmain.f()\n\t/a.go:5 +0x2\n\ngoroutine 3 [select]:\nmain.g()\n\t/a.go:9 +0x3\n", False),
        Fixture("input that already contains a group line", 'x3 threads "chan receive": 4-6\nmain.worker(0x0)\n\t/a.go:1 +0x1\n\n' + GOSTACK[:900], None),
        Fixture("thread name with a quote and a comma", 'goroutine 5 [select]:\nmain.f()\n\ngoroutine 6 [select]:\nmain.f()\n\n"a\\"b, c" #3 [1] prio=5 os_prio=0 tid=0x1 nid=0x2 runnable  [0x3]\n\tat X.y(X.java:1)\n\n"a\\"b, d" #4 [1] prio=5 os_prio=0 tid=0x1 nid=0x3 runnable  [0x3]\n\tat X.y(X.java:1)\n', None),
    ],
    min_gain=0.15,
)

# ===============================================================================================================
# #328 Collapse diff hunks of generated paths to 'path +N -M generated'.
# `git check-attr linguist-generated` is modelled by GENERATED_GLOBS (the .gitattributes of the capture repo plus the usual
# suspects); a real implementation asks git. Only well-formed sections (header lines, hunks whose line counts match their
# @@ header) are folded; anything else is left verbatim, so a truncated diff is never half-rewritten.

GENERATED_GLOBS = ["api/v1/*.pb.go", "dist/*", "*.min.js", "*.min.css", "*.snap", "*.pb.go", "*_pb2.py", "package-lock.json",
                   "yarn.lock", "pnpm-lock.yaml", "Cargo.lock", "poetry.lock", "go.sum", "*.js.map"]
_DIFF_HDR = re.compile(r"^diff --git a/(.+) b/(.+)$")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_FOLDED = re.compile(r"^([^\s+\-@\\].*) \+(\d+) -(\d+) generated(?: \((new file|deleted)\))?$")
_PRE = ("index ", "new file mode ", "deleted file mode ", "old mode ", "new mode ", "--- ", "+++ ")


def is_generated(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, g) for g in GENERATED_GLOBS)


def _split_sections(text: str) -> list[list[str]]:
    """Lines of the text cut into sections at every 'diff --git' line (the first section may be preamble)."""
    secs: list[list[str]] = [[]]
    for line in text.split("\n"):
        if line.startswith("diff --git ") and secs[-1]:
            secs.append([])
        secs[-1].append(line)
    return secs


def _fold_section(sec: list[str]):
    """-> (summary line) if the section is a well-formed diff of a generated path, else None."""
    m = _DIFF_HDR.match(sec[0])
    if not m or m.group(1) != m.group(2) or not is_generated(m.group(1)) or m.group(1)[0] in " +-@\\":
        return None
    path, i, kind = m.group(1), 1, ""
    while i < len(sec) and sec[i].startswith(_PRE):
        if sec[i].startswith("new file mode "):
            kind = " (new file)"
        elif sec[i].startswith("deleted file mode "):
            kind = " (deleted)"
        i += 1
    plus = minus = 0
    nh = 0
    while i < len(sec):
        h = _HUNK.match(sec[i])
        if not h:
            break
        old_n, new_n = int(h.group(2) if h.group(2) is not None else 1), int(h.group(4) if h.group(4) is not None else 1)
        i += 1
        nh += 1
        while old_n > 0 or new_n > 0:
            if i >= len(sec):
                return None
            ch = sec[i][:1]
            if ch == " ":
                old_n, new_n = old_n - 1, new_n - 1
            elif ch == "-":
                old_n, minus = old_n - 1, minus + 1
            elif ch == "+":
                new_n, plus = new_n - 1, plus + 1
            else:
                return None
            i += 1
            if old_n < 0 or new_n < 0:
                return None
        while i < len(sec) and sec[i].startswith("\\"):
            i += 1
    if nh == 0 or not (i == len(sec) or (i == len(sec) - 1 and sec[i] == "")):
        return None
    return f"{path} +{plus} -{minus} generated{kind}"


def fold_generated(text: str) -> str:
    secs = _split_sections(text)
    out: list[str] = []
    changed = False
    for k, sec in enumerate(secs):
        s = _fold_section(sec) if sec and sec[0].startswith("diff --git ") else None
        if s is None:
            out += sec
        else:
            changed = True
            out.append(s)
            if sec[-1] == "" and k == len(secs) - 1:
                out.append("")  # keep the final newline of the whole text
    if not changed:
        return text
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def probes_328(text: str) -> dict:
    """Sectioner view: per file (path, +N, -M) by prefix counting after the first hunk header; verbatim lines of every
    non-generated section; any text outside sections."""
    files: list[tuple] = []
    verbatim: list[tuple] = []
    outside: list[str] = []
    cur: list[str] | None = None

    def close():
        if cur is None:
            return
        m = _DIFF_HDR.match(cur[0])
        path = m.group(2) if m else cur[0]
        k = next((j for j, l in enumerate(cur) if l.startswith("@@")), len(cur))
        body = cur[k:]
        files.append((path, sum(l.startswith("+") for l in body), sum(l.startswith("-") for l in body)))
        if not is_generated(path):
            verbatim.append(tuple(cur))

    for line in text.split("\n"):
        f = _FOLDED.match(line)
        if line.startswith("diff --git "):
            close()
            cur = [line]
        elif f:
            close()
            cur = None
            files.append((f.group(1), int(f.group(2)), int(f.group(3))))
        elif cur is not None:
            cur.append(line)
        else:
            outside.append(line)
    close()
    # a trailing empty element (final newline) belongs to the last section or to nothing; normalise it away
    verbatim = [v[:-1] if v and v[-1] == "" else v for v in verbatim]
    while outside and outside[-1] == "":
        outside.pop()
    return {
        "files with +added -removed counts": files,
        "verbatim diff of every non-generated file": verbatim,
        "text outside any diff section": outside,
    }


def _sections_of(diff: str) -> list[str]:
    return ["\n".join(s) for s in _split_sections(diff.rstrip("\n"))]


_GS = _sections_of(GEN_DIFF)
_GS_BY = {s.split("\n")[0].split(" b/")[-1]: s for s in _GS}
_NEW_SNAP = ("diff --git a/tests/__snapshots__/old.test.js.snap b/tests/__snapshots__/old.test.js.snap\ndeleted file mode 100644\nindex 3b18e51..0000000\n"
             "--- a/tests/__snapshots__/old.test.js.snap\n+++ /dev/null\n@@ -1,3 +0,0 @@\n-// Jest Snapshot v1\n-\n-exports[`a 1`] = `1`;")
case(
    328,
    claim="Sections of a git diff whose path is generated (check-attr stand-in: dist/, *.min.js, *.snap, *.pb.go, lockfiles) become 'path +N -M generated'; every non-generated file's diff is "
          "byte-identical and every file keeps its added/removed counts. Malformed or truncated sections are left verbatim.",
    loses="the hunk text, hunk headers and blob ids of generated files (their +/- counts and the fact that they changed are kept)",
    encode=fold_generated,
    probes=probes_328,
    fixtures=[
        Fixture("real diff: .pb.go, minified bundle, svg (-diff), lockfile, real code, snapshot, new test", GEN_DIFF, True),
        Fixture("real code change first, generated last", "\n".join([_GS_BY["src/server.py"], _GS_BY["api/v1/orders.pb.go"], _GS_BY["dist/bundle.min.js"], _GS_BY["tests/__snapshots__/orders.test.js.snap"]]) + "\n", True),
        Fixture("generated first, real code last", "\n".join([_GS_BY["package-lock.json"], _GS_BY["tests/__snapshots__/orders.test.js.snap"], _GS_BY["tests/test_server.py"]]) + "\n", True),
        Fixture("deleted snapshot file between real changes", "\n".join([_GS_BY["src/server.py"], _NEW_SNAP, _GS_BY["api/v1/orders.pb.go"], _GS_BY["tests/test_server.py"]]) + "\n", True),
        Fixture("real diff with no generated file (httpcore2 rename)", RENAME_DIFF, False),
        Fixture("one real change", _GS_BY["src/server.py"] + "\n", False),
        Fixture("generated path text inside a real hunk", "diff --git a/README.md b/README.md\nindex 1..2 100644\n--- a/README.md\n+++ b/README.md\n@@ -1,2 +1,2 @@\n-see dist/app.min.js +3 -1 generated\n+see dist/app.min.js +4 -1 generated\n x\n", False),
    ],
    min_gain=0.2,
)

# ===============================================================================================================
# #347 Clean YouTube-style VTT and SRT: merge rolling cues, drop inline tags and positioning, keep a [mm:ss] stamp per 30 s.
# Semantics shared by encoder and probes: a cue's text lines are tag-stripped and whitespace-normalised; leading lines that
# repeat the previous cue's trailing lines (line-level suffix/prefix overlap, as in rolling auto-captions) are not new.
# The probe reads cues with a block state machine; the encoder splits on blank lines and recurses over overlap sizes.

_TAG = re.compile(r"</?(?:c|v|i|b|u|font|ruby|rt|lang)(?:[. ][^>]*)?>|<\d{2}:\d{2}:\d{2}\.\d{3}>|<\d{1,2}:\d{2}\.\d{3}>")
_TIMING = re.compile(r"^\s*(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+\S+")
_STAMPED = re.compile(r"^\[(?:(\d+):)?(\d{2}):(\d{2})\] (.*)$")


def _secs(h, m, s) -> int:
    return int(h or 0) * 3600 + int(m) * 60 + int(s)


def _stamp(sec: int) -> str:
    return f"[{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}]" if sec >= 3600 else f"[{sec // 60:02d}:{sec % 60:02d}]"


def _norm_line(l: str) -> str:
    return " ".join(_TAG.sub("", l).split())


def _cues(text: str) -> list[tuple[int, list[str]]]:
    """(start second, normalised non-empty text lines) of every cue block, by blank-line splitting."""
    out = []
    for block in _blocks(text):
        for k in (0, 1):
            if k < len(block) and (m := _TIMING.match(block[k])):
                lines = [n for n in map(_norm_line, block[k + 1:]) if n]
                out.append((_secs(m.group(1), m.group(2), m.group(3)), lines))
                break
    return out


def _blocks(text: str) -> list[list[str]]:
    blocks, cur = [], []
    for line in text.split("\n"):
        line = line[:-1] if line.endswith("\r") else line
        if line == "":
            if cur:
                blocks.append(cur)
            cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def _overlap(prev: list[str], cur: list[str]) -> int:
    for j in range(min(len(prev), len(cur)), 0, -1):
        if prev[-j:] == cur[:j]:
            return j
    return 0


def clean_captions(text: str) -> str:
    cues = _cues(text)
    if not cues:
        return text
    runs: list[tuple[int, list[str]]] = []  # consecutive cues in the same 30 s bucket share one stamped line (order is never changed)
    prev: list[str] = []
    for start, lines in cues:
        new = lines[_overlap(prev, lines):]
        prev = lines
        if new:
            b = start // 30 * 30
            if runs and runs[-1][0] == b:
                runs[-1][1].extend(new)
            else:
                runs.append((b, list(new)))
    out = "\n".join(f"{_stamp(b)} {' '.join(ls)}" for b, ls in runs)
    if not out:
        return text
    out += "\n" if text.endswith("\n") else ""
    return out if len(out) < len(text) else text


def probes_347(text: str) -> dict:
    """Reader view: ordered spoken words and, per 30 s bucket, what was said (from cues, or from stamped lines)."""
    rows: list[tuple[int, str]] = []
    cues = _cues(text)
    if cues:
        prev: list[str] = []
        for start, lines in cues:
            k = max(k for k in range(0, min(len(prev), len(lines)) + 1) if prev[len(prev) - k:] == lines[:k])
            prev = lines
            for l in lines[k:]:
                rows.append((start // 30 * 30, l))
    else:
        for line in text.split("\n"):
            if m := _STAMPED.match(line):
                rows.append((_secs(m.group(1), m.group(2), m.group(3)), m.group(4)))
    merged: list[tuple[int, list[str]]] = []
    for b, l in rows:
        if merged and merged[-1][0] == b:
            merged[-1][1].append(l)
        else:
            merged.append((b, [l]))
    words = [w for b, l in rows for w in l.split()]
    return {
        "ordered words": words,
        "text per 30 s bucket, in order": [(b, " ".join(" ".join(v).split())) for b, v in merged],
    }


_TALK = (
    "okay welcome back everyone so today I want to walk through the migration plan for the billing service and the migration deadline is March 14th "
    "so we have about six weeks the first thing to understand is that the old invoice table has three different date formats which is why the import "
    "job keeps failing on row four thousand and twelve we looked at the logs yesterday and the root cause is a timezone offset that was stored as text "
    "now the fix is not complicated we parse the offset normalise it to UTC and write it back but we have to do that for about two million rows "
    "and we cannot lock the table while we do it so the plan is to batch the work in chunks of ten thousand rows with a short sleep between each batch "
    "the second thing is the customer facing API we decided to keep version one alive until June and add a deprecation header that points to the new "
    "documentation Maria will own the documentation and Joel will own the client libraries for Python and Go and I will take the rollout plan "
    "and the communication to the largest accounts there are seventeen accounts that account for most of the traffic so we will email each of them "
    "personally before the header goes live then we monitor error rates for a week before we turn anything off "
    "okay a quick word on testing we agreed to run the new pipeline in shadow mode for two weeks and compare totals every night any difference above "
    "one cent per invoice stops the rollout automatically and pages whoever is on call that is the safety net and I really want everybody to be comfortable with it "
    "to wrap up the three decisions are these we freeze the schema on March first we switch traffic on April second and we remove the old tables on May thirtieth "
    "thanks everyone and see you at the next review"
).split()


def _vtt_time(t: float, comma: bool = False, hours: bool = True) -> str:
    ms = int(round(t * 1000))
    h, r = divmod(ms, 3600000)
    m, r = divmod(r, 60000)
    s, ms = divmod(r, 1000)
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}" if hours else f"{m:02d}:{s:02d}{sep}{ms:03d}"


def _talk_lines(words: list[str], per_line: int = 6) -> list[list[tuple[str, float]]]:
    t, lines, cur = 0.0, [], []
    for i, w in enumerate(words):
        cur.append((w, t))
        t += 0.31 + 0.037 * (len(w) % 5) + (0.9 if w in ("so", "okay", "now") else 0.0)
        if len(cur) == per_line:
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    return lines


def make_youtube_vtt(words: list[str], offset: float = 0.0) -> str:
    """YouTube auto-caption VTT shape (yt-dlp --write-auto-subs): header, rolling cue [previous line, tagged new line], then a 10 ms static cue."""
    lines = _talk_lines(words)
    out = ["WEBVTT", "Kind: captions", "Language: en", ""]
    prev_text = " "
    for k, ln in enumerate(lines):
        start = ln[0][1] + offset
        end = (lines[k + 1][0][1] if k + 1 < len(lines) else ln[-1][1] + 0.6) + offset
        tagged = ln[0][0] + "".join(f"<{_vtt_time(t + offset)}><c> {w}</c>" for w, t in ln[1:])
        text = " ".join(w for w, _ in ln)
        out += [f"{_vtt_time(start)} --> {_vtt_time(end - 0.01)} align:start position:0%", prev_text, tagged, ""]
        out += [f"{_vtt_time(end - 0.01)} --> {_vtt_time(end)} align:start position:0%", text, " ", ""]
        prev_text = text
    return "\n".join(out)


def make_srt(words: list[str], offset: float = 0.0, per_line: int = 5) -> str:
    """Plain two-line SRT with numbered cues (no repeats)."""
    lines = _talk_lines(words, per_line)
    out = []
    for n in range(0, len(lines), 2):
        pair = lines[n:n + 2]
        start, end = pair[0][0][1] + offset, pair[-1][-1][1] + 0.4 + offset
        out += [str(n // 2 + 1), f"{_vtt_time(start, True)} --> {_vtt_time(end, True)}"] + [" ".join(w for w, _ in l) for l in pair] + [""]
    return "\n".join(out)


_YT = make_youtube_vtt(_TALK)
case(
    347,
    claim="Caption cues (YouTube rolling VTT or SRT) are merged into '[mm:ss] words' lines, one per 30 s bucket of cue start: inline <time><c> tags, positioning, cue ids and the rolling repeats of the previous line are dropped; "
          "the ordered spoken words and which 30 s bucket each cue's new words belong to are unchanged.",
    loses="cue end times and sub-30 s timing, per-word timestamps, styling and positioning, NOTE/STYLE blocks, and a cue whose first lines legitimately repeat the previous cue's last lines (read as a rolling repeat)",
    encode=clean_captions,
    probes=probes_347,
    fixtures=[
        Fixture("YouTube auto-caption VTT, 3.5 minutes", _YT, True),
        Fixture("same, an hour into the video ([h:mm:ss] stamps)", make_youtube_vtt(_TALK, offset=3725.0), True),
        Fixture("plain numbered SRT, two-line cues", make_srt(_TALK), True),
        Fixture("VTT with cue ids, a NOTE and CRLF line endings", "WEBVTT\r\n\r\nNOTE made by hand\r\n\r\nintro\r\n00:00:01.000 --> 00:00:03.000\r\nWelcome back, everyone.\r\n\r\nq1\r\n00:00:31.500 --> 00:00:34.000\r\nThe deadline is March 14th.\r\nSee the <b>plan</b>.\r\n\r\n00:01:02.000 --> 00:01:04.000\r\nThanks for watching.\r\n", True),
        Fixture("already-cleaned transcript", "[00:00] welcome back everyone\n[00:30] the deadline is March 14th\n", False),
        Fixture("text with arrows but no timing lines", "a --> b\nstep one --> step two\n", False),
        Fixture("input that looks like our own output plus a cue", "[00:30] earlier text\n\n00:00:01.000 --> 00:00:02.000\nhi there\n", None),
    ],
    min_gain=0.3,
)

# ===============================================================================================================
# #348 Whisper / ASR JSON -> '[mm:ss] speaker: text', merging same-speaker segments, flagging only low-confidence segments.
# Lines break on a speaker change, on a flagged segment (it keeps its own stamp and its three confidence numbers), and when a
# line would span 30 s. Token-id arrays, per-word records and the duplicated top-level 'text' are dropped (the top-level text
# must equal the joined segments, otherwise nothing is rewritten). Whisper CLI output is a single JSON line.

_W_SEG_KEYS = {"id", "seek", "start", "end", "text", "tokens", "temperature", "avg_logprob", "compression_ratio", "no_speech_prob", "words", "speaker"}
_W_TOP_KEYS = {"text", "segments", "language", "word_segments"}
_SPEAKER_OK = re.compile(r"^[A-Za-z0-9_.\- ]{1,40}$")
_W_LINE = re.compile(r"^\[(?:(\d+):)?(\d{2}):(\d{2})\] (.*)$")
_W_FLAG = re.compile(r"^\?\(([^)]*)\) (.*)$")


def _flag_info(seg: dict) -> str | None:
    lp, ns, cr = seg.get("avg_logprob"), seg.get("no_speech_prob"), seg.get("compression_ratio")
    bad = (isinstance(lp, (int, float)) and lp < -1.0) or (isinstance(ns, (int, float)) and ns > 0.6) or (isinstance(cr, (int, float)) and cr > 2.4)
    if not bad:
        return None
    parts = []
    for tag, v in (("lp", lp), ("nsp", ns), ("cr", cr)):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            parts.append(f"{tag}={v:.2f}")
    return " ".join(parts)


def _whisper_ok(d) -> list | None:
    if not isinstance(d, dict) or not isinstance(d.get("segments"), list) or not d["segments"] or set(d) - _W_TOP_KEYS:
        return None
    segs = d["segments"]
    for s in segs:
        if not isinstance(s, dict) or set(s) - _W_SEG_KEYS or not isinstance(s.get("text"), str):
            return None
        if isinstance(s.get("start"), bool) or not isinstance(s.get("start"), (int, float)) or not math.isfinite(s["start"]) or s["start"] < 0 or s["start"] > 10**7:
            return None
        sp = s.get("speaker")
        if "speaker" in s and (not isinstance(sp, str) or not _SPEAKER_OK.match(sp) or sp.strip() != sp):
            return None
        for k in ("avg_logprob", "no_speech_prob", "compression_ratio"):
            if k in s and (isinstance(s[k], bool) or not isinstance(s[k], (int, float)) or not math.isfinite(s[k])):
                return None
        if "?(" in s["text"]:
            return None
    if "text" in d and (not isinstance(d["text"], str) or d["text"].split() != [w for s in segs for w in s["text"].split()]):
        return None
    if "language" in d and (not isinstance(d["language"], str) or not re.fullmatch(r"[A-Za-z\-]{2,12}", d["language"])):
        return None
    names = {s["speaker"] for s in segs if "speaker" in s}
    if any(", " in n for n in names):
        return None
    for s in segs:
        t = s["text"].strip()
        if "speaker" not in s and any(t.startswith(n + ": ") for n in names):
            return None
    return segs


def whisper_to_text(text: str) -> str:
    try:
        d = json.loads(text)
    except ValueError:
        return text
    segs = _whisper_ok(d)
    if segs is None:
        return text
    lines: list[list] = []  # [start, speaker, flagged info, words]
    for s in segs:
        words = s["text"].split()
        if not words:
            continue
        sp, fl, st = s.get("speaker"), _flag_info(s), int(s["start"])
        if lines and not fl and lines[-1][2] is None and lines[-1][1] == sp and st - lines[-1][0] < 30:
            lines[-1][3].extend(words)
        else:
            lines.append([st, sp, fl, words])
    if not lines:
        return text
    head = []
    if "language" in d:
        head.append(f"language: {d['language']}")
    names = sorted({s["speaker"] for s in segs if "speaker" in s})
    if names:
        head.append("speakers: " + ", ".join(names))
    body = []
    for st, sp, fl, words in lines:
        body.append(f"{_stamp(st)} " + (f"{sp}: " if sp is not None else "") + (f"?({fl}) " if fl else "") + " ".join(words))
    out = "\n".join(head + body) + ("\n" if text.endswith("\n") else "")
    return out if len(out) < len(text) else text


def probes_348(text: str) -> dict:
    """Reader view from either form: language, speaker legend, and per line (stamp, speaker, flag numbers, words)."""
    rows: list[tuple] = []
    language, names = None, None
    try:
        d = json.loads(text)
    except ValueError:
        d = None
    segs = d.get("segments") if isinstance(d, dict) else None
    if isinstance(segs, list) and segs and all(isinstance(s, dict) and isinstance(s.get("start"), (int, float)) and isinstance(s.get("text"), str) for s in segs):
        language = d.get("language")
        cur = None
        for s in segs:
            words = s["text"].split()
            if not words:
                continue
            sp, fl, st = s.get("speaker"), _flag_info(s), int(s["start"])
            if cur is not None and fl is None and cur[2] is None and cur[1] == sp and st - cur[0] < 30:
                cur[3].extend(words)
            else:
                cur = [st, sp, fl, list(words)]
                rows.append(cur)
        rows = [tuple(r[:3]) + (" ".join(r[3]),) for r in rows]
    else:
        lines = text.split("\n")
        k = 0
        while k < len(lines) and not _W_LINE.match(lines[k]):
            if lines[k].startswith("language: "):
                language = lines[k][10:]
            elif lines[k].startswith("speakers: "):
                names = lines[k][10:].split(", ")
            k += 1
        for line in lines[k:]:
            m = _W_LINE.match(line)
            if not m:
                continue
            rest, fl, sp = m.group(4), None, None
            for n in names or []:
                if rest.startswith(n + ": "):
                    sp, rest = n, rest[len(n) + 2:]
                    break
            f = _W_FLAG.match(rest)
            if f:
                fl, rest = f.group(1), f.group(2)
            rows.append((_secs(m.group(1), m.group(2), m.group(3)), sp, fl, rest))
    return {
        "language": language,
        "lines (stamp, speaker, low-confidence numbers, text)": rows,
        "ordered words": [w for r in rows for w in r[3].split()],
    }


def make_whisper_json(words: list[str], diarized: bool = False, word_ts: bool = False, indent=None, flagged=(), hallucination=False) -> str:
    lines = _talk_lines(words, 11)
    segs = []
    for i, ln in enumerate(lines):
        start, end = ln[0][1], (lines[i + 1][0][1] if i + 1 < len(lines) else ln[-1][1] + 0.5)
        txt = " " + " ".join(w for w, _ in ln)
        toks = [50364 + 7 * i] + [(sum(map(ord, w)) * 31 + k * 977) % 50000 + 200 for w, _ in ln for k in range(1 + len(w) // 6)] + [50364 + 7 * i + 1]
        seg = {"id": i, "seek": 0 if start < 29 else int(start // 30) * 3000, "start": round(start, 2), "end": round(end, 2), "text": txt, "tokens": toks,
               "temperature": 0.0, "avg_logprob": round(-0.18 - 0.011 * (i % 7), 4), "compression_ratio": round(1.45 + 0.02 * (i % 5), 4), "no_speech_prob": round(0.01 + 0.002 * (i % 3), 5)}
        if i in flagged:
            seg["avg_logprob"], seg["no_speech_prob"] = -1.31, 0.12
        if word_ts:
            seg["words"] = [{"word": " " + w, "start": round(t, 2), "end": round(t + 0.3, 2), "probability": round(0.9 - 0.01 * (len(w) % 6), 4)} for w, t in ln]
        if diarized:
            seg["speaker"] = "SPEAKER_00" if (i // 3) % 2 == 0 else "SPEAKER_01"
        segs.append(seg)
    if hallucination:
        t = lines[-1][-1][1] + 2.0
        segs.append({"id": len(segs), "seek": 0, "start": round(t, 2), "end": round(t + 2, 2), "text": " Thank you.", "tokens": [50364, 1044, 291, 13, 50464], "temperature": 0.0,
                     "avg_logprob": -1.52, "compression_ratio": 0.7, "no_speech_prob": 0.93})
    top = {"text": "".join(s["text"] for s in segs), "segments": segs, "language": "en"}
    return json.dumps(top, ensure_ascii=False, indent=indent)


_WH = make_whisper_json(_TALK, flagged=(0, 9, 20), hallucination=True)
case(
    348,
    claim="Whisper-style ASR JSON becomes '[mm:ss] speaker: text' lines: same-speaker segments merge (30 s per line), every spoken word stays in order with the stamp of its line, the speaker of each word, "
          "the language, and every segment under Whisper's own confidence thresholds is flagged on its own stamped line with its avg_logprob, no_speech_prob and compression_ratio.",
    loses="token-id arrays, temperature, seek, segment end times, per-word timestamps and probabilities, the duplicated top-level text, segment ids, and the stamps of merged segments after the first of each line",
    encode=whisper_to_text,
    probes=probes_348,
    fixtures=[
        Fixture("whisper CLI json, one flagged segment first, middle and last plus a hallucinated tail", _WH, True),
        Fixture("diarized with SPEAKER_00 / SPEAKER_01", make_whisper_json(_TALK, diarized=True, flagged=(4,)), True),
        Fixture("word timestamps (--word_timestamps True)", make_whisper_json(_TALK[:140], word_ts=True), True),
        Fixture("pretty-printed (indent=2) with a trailing newline", make_whisper_json(_TALK[:90], indent=2) + "\n", True),
        Fixture("json that is not a transcript", json.dumps({"segments": "none", "text": "x"}), False),
        Fixture("transcript whose top-level text disagrees with its segments", json.dumps({"text": " changed", "segments": [{"start": 0.0, "text": " original words here", "tokens": list(range(40))}]}), False),
        Fixture("segment with an unknown key (kept verbatim, nothing silently dropped)", json.dumps({"segments": [{"start": 0.0, "text": " hello there", "tokens": list(range(40)), "novel_field": 1}]}), False),
        Fixture("text that starts like a flag marker", json.dumps({"segments": [{"start": 0.0, "text": " ?(lp=1) hi", "tokens": list(range(30))}]}), None),
    ],
    min_gain=0.4,
)

# ===============================================================================================================
# #350 Compact accessibility trees (Playwright MCP snapshot YAML): collapse generic wrapper chains, drop refs on
# non-interactive nodes, fold '- /url:' into the link line and cap long hrefs, cap long text-only lists, stub a landmark
# subtree that repeats an earlier one in the same snapshot. 'Repeated from an earlier page' needs cross-call state and is
# out of scope for a pure text transform (the in-snapshot duplicate, e.g. a mobile nav, is the part that can be tested here).

_A_LINE = re.compile(r"^( *)- (?=[a-z/])(.*)$")
_A_NODE = re.compile(r'^([a-z]+)((?: "(?:[^"\\]|\\.)*")?)((?: \[[^\]\n]*\])*)(?: -> (\S+))?(:(?: .*)?)?$')
_A_REF = re.compile(r" \[ref=(e\d+)\]")
A_INTERACTIVE = {"link", "button", "textbox", "checkbox", "radio", "combobox", "searchbox", "slider", "spinbutton", "switch", "tab", "menuitem",
                 "menuitemcheckbox", "menuitemradio", "option", "treeitem", "listbox", "iframe"}
A_LANDMARKS = {"banner", "navigation", "contentinfo", "complementary", "search"}
A_LIST_K = 6
A_HREF_MAX = 64
_STUB = re.compile(r"^\(same as landmark #(\d+) above\)$")


class _N:
    __slots__ = ("role", "name", "attrs", "url", "tail", "children", "raw", "indent")

    def __init__(self, raw: str, indent: int):
        self.raw, self.indent, self.children = raw, indent, []
        self.role = self.name = self.url = self.tail = None
        self.attrs: list[str] = []
        if raw.startswith("/url: "):
            self.role, self.url = "/url", raw[6:]
            return
        m = _A_NODE.match(raw)
        if m:
            self.role, self.name, self.url, self.tail = m.group(1), m.group(2)[2:-1] if m.group(2) else None, m.group(4), m.group(5) or ""
            self.attrs = re.findall(r"\[[^\]\n]*\]", m.group(3))

    @property
    def ok(self) -> bool:
        return self.role is not None

    def head(self) -> str:
        """Line content rebuilt from the parsed fields."""
        if self.role == "/url":
            return "/url: " + self.url
        s = self.role + (f' "{self.name}"' if self.name is not None else "") + "".join(" " + a for a in self.attrs)
        tail = self.tail or ""
        if self.url is not None and self.role != "/url":
            s += f" -> {self.url}"
            if tail == ":":
                tail = ""  # the url ends the line; children are known from indentation
        return s + tail


def _a_parse(lines: list[str]):
    """-> list of root nodes, or None if indentation is not a clean 2-space tree or a line is not a node."""
    roots: list[_N] = []
    stack: list[_N] = []
    for l in lines:
        m = _A_LINE.match(l)
        if not m or len(m.group(1)) % 2:
            return None
        n = _N(m.group(2), len(m.group(1)) // 2)
        if not n.ok:
            return None
        while stack and stack[-1].indent >= n.indent:
            stack.pop()
        if n.indent == 0:
            roots.append(n)
        elif not stack or n.indent != stack[-1].indent + 1:
            return None
        else:
            stack[-1].children.append(n)
        stack.append(n)
    return roots


def _a_render(n: _N, depth: int, out: list[str]) -> None:
    out.append("  " * depth + "- " + n.head())
    for c in n.children:
        _a_render(c, depth + 1, out)


def _interactive(n: _N) -> bool:
    return n.role in A_INTERACTIVE or "[cursor=pointer]" in n.attrs


def _has_interactive(n: _N) -> bool:
    return _interactive(n) or any(_has_interactive(c) for c in n.children)


def _cap_href(u: str) -> str:
    return u if len(u) <= A_HREF_MAX else u[: A_HREF_MAX - 3] + "..."


def _sig(n: _N) -> str:
    out: list[str] = []
    _a_render(n, 0, out)
    return _A_REF.sub("", "\n".join(out))


def _a_transform(roots: list[_N], seen: dict[str, int], count: list[int]) -> list[_N]:
    def fold_url(n: _N) -> None:
        for c in n.children:
            fold_url(c)
        if n.role == "link" and n.children and n.children[0].role == "/url" and n.tail in ("", ":") and n.url is None and n.children[0].url and not any(ch.isspace() for ch in n.children[0].url):
            n.url = _cap_href(n.children[0].url)
            n.children = n.children[1:]
            if not n.children and n.tail == ":":
                n.tail = ""

    def collapse(n: _N) -> _N:
        n.children = [collapse(c) for c in n.children]
        while n.role == "generic" and n.name is None and n.tail == ":" and len(n.children) == 1 and all(a.startswith("[ref=") for a in n.attrs):
            n = n.children[0]
        return n

    def stub_dupes(n: _N) -> _N:
        if n.role in A_LANDMARKS and n.tail is not None and n.children:
            sig = _sig(n)
            if sig in seen and sig.count("\n") >= 3:
                n.children, n.tail = [], f": (same as landmark #{seen[sig]} above)"
                return n
            count[0] += 1
            seen.setdefault(sig, count[0])
        n.children = [stub_dupes(c) for c in n.children]
        return n

    def cap_lists(n: _N) -> None:
        for c in n.children:
            cap_lists(c)
        if n.role == "list" and len(n.children) > A_LIST_K + 1 and not any(_has_interactive(c) for c in n.children[A_LIST_K:]):
            extra = len(n.children) - A_LIST_K
            more = _N(f"more: +{extra} items omitted (no interactive elements)", n.indent + 1)
            n.children = n.children[:A_LIST_K] + [more]

    def drop_refs(n: _N) -> None:
        if not _interactive(n):
            n.attrs = [a for a in n.attrs if not a.startswith("[ref=")]
        for c in n.children:
            drop_refs(c)

    for r in roots:
        fold_url(r)
    roots = [collapse(r) for r in roots]
    roots = [stub_dupes(r) for r in roots]
    for r in roots:
        cap_lists(r)
        drop_refs(r)
    return roots


def compact_a11y(text: str) -> str:
    lines = text.split("\n")
    if any(re.match(r"^ *- more: ", l) for l in lines):
        return text
    out: list[str] = []
    seen: dict[str, int] = {}
    count = [0]
    i = 0
    while i < len(lines):
        if _A_LINE.match(lines[i]):
            j = i
            while j < len(lines) and _A_LINE.match(lines[j]):
                j += 1
            roots = _a_parse(lines[i:j])
            if roots is None:
                out += lines[i:j]
            else:
                for r in _a_transform(roots, seen, count):
                    _a_render(r, 0, out)
            i = j
        else:
            out.append(lines[i])
            i += 1
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def probes_350(text: str) -> dict:
    """Tree reader: interactive elements (role, name, url) with their first ref, and the non-wrapper content with its ancestor chain."""
    lines = text.split("\n")
    content: list = []
    all_roots: list[_N] = []
    i = 0
    while i < len(lines):
        if _A_LINE.match(lines[i]):
            j = i
            while j < len(lines) and _A_LINE.match(lines[j]):
                j += 1
            roots = _a_parse(lines[i:j])
            if roots is None:
                content.append(("unparsed run", tuple(lines[i:j])))
            else:
                all_roots += roots
            i = j
        else:
            content.append(("other line", lines[i]))
            i += 1
    landmarks: list = []  # pre-order slots: (chain length, interactive entries, content records) of each non-stub landmark

    def walk(n: _N, chain: tuple, inter: list, cont: list) -> None:
        if n.role == "/url":
            return
        ref = _A_REF.search(" ".join(n.attrs))
        url = n.url
        for c in n.children:
            if c.role == "/url":
                url = c.url
        is_inter = n.role in A_INTERACTIVE or "[cursor=pointer]" in n.attrs
        wrapper = n.role == "generic" and n.name is None and (n.tail or "") in ("", ":") and all(a.startswith("[ref=") for a in n.attrs)
        text_ = n.tail[2:] if n.tail and n.tail.startswith(": ") else None
        if is_inter:
            inter.append(((n.role, n.name, url[:50] if url else url), ref.group(1) if ref else None))
        if n.role in A_LANDMARKS and text_ is not None and _STUB.match(text_) and not n.children:
            k = int(_STUB.match(text_).group(1))
            if 0 < k <= len(landmarks) and landmarks[k - 1] is not None:
                clen, src_inter, src_cont = landmarks[k - 1]
                inter.extend((key, None) for key, _ in src_inter)
                cont.extend((chain + rec[0][clen:],) + rec[1:] for rec in src_cont)
            return
        if n.role == "more" and text_ is not None and (m := re.match(r"\+(\d+) ", text_)):
            cont.append((chain, "more", int(m.group(1))))
            return
        slot = None
        if n.role in A_LANDMARKS:
            slot = len(landmarks)
            landmarks.append(None)
        i0, c0 = len(inter), len(cont)
        if not wrapper:
            lvl = next((a for a in n.attrs if a.startswith("[level=")), None)
            cont.append((chain, n.role, n.name, text_, lvl))
        sub = chain if wrapper else chain + ((n.role, n.name),)
        kids = [c for c in n.children if c.role != "/url"]
        if n.role == "list" and len(kids) > A_LIST_K:
            more = [c for c in kids if c.role == "more"]
            real = [c for c in kids if c.role != "more"]
            extra = int(re.match(r": \+(\d+) ", more[0].tail).group(1)) if more and more[0].tail and re.match(r": \+(\d+) ", more[0].tail) else 0
            cont.append((chain, "list size", len(real) + extra))
            for c in real[:A_LIST_K]:
                walk(c, sub, inter, cont)
            for c in real[A_LIST_K:]:  # extras are never compared by content (the rule may have folded them), interactive ones are kept
                walk(c, sub, inter, [])
        else:
            for c in kids:
                walk(c, sub, inter, cont)
        if slot is not None:
            landmarks[slot] = (len(chain), inter[i0:], cont[c0:])

    interactive: Counter = Counter()
    first_ref: dict = {}
    for r in all_roots:
        inter: list = []
        walk(r, (), inter, content)
        for key, ref in inter:
            interactive[key] += 1
            if ref and key not in first_ref:
                first_ref[key] = ref
    return {
        "interactive elements (role, name, first 50 chars of href) with counts": sorted(interactive.items(), key=str),
        "first ref of each interactive element": sorted(first_ref.items(), key=str),
        "page content with ancestor chains": content,
    }


class _Gen:
    """Builds Playwright-MCP-shaped snapshots (shape from memory of browser_snapshot output) with sequential refs."""

    def __init__(self):
        self.n = 0
        self.lines: list[str] = []

    def node(self, depth: int, role: str, name: str | None = None, attrs: str = "", text: str | None = None, url: str | None = None, ref: bool = True, kids=None):
        self.n += 1
        cursor = " [cursor=pointer]" if "[cursor=pointer]" in attrs else ""
        s = "  " * depth + f"- {role}" + (f' "{name}"' if name is not None else "") + attrs.replace(" [cursor=pointer]", "") + (f" [ref=e{self.n}]" if ref else "") + cursor
        has_kids = bool(kids) or url is not None
        s += ":" if (has_kids and text is None) else (f": {text}" if text is not None else "")
        self.lines.append(s)
        if url is not None:
            self.lines.append("  " * (depth + 1) + f"- /url: {url}")
        if kids:
            kids(depth + 1)

    def text(self) -> str:
        return "\n".join(self.lines)


def make_snapshot(extra_footer_links: int = 0, mobile_nav: bool = True, changelog: int = 14, dialog: bool = True) -> str:
    g = _Gen()
    nav_items = [("Guides", "/guides"), ("API reference", "/api"), ("Pricing", "/pricing?utm_source=docs&utm_medium=nav&utm_campaign=spring-2026&ref=docs-header-0042"), ("Changelog", "/changelog"),
                 ("Status", "https://status.example.com/"), ("Community", "/community"), ("Blog", "/blog"), ("Sign in", "/login")]

    def nav(d):
        def lst(d):
            for label, href in nav_items:
                g.node(d, "listitem", kids=lambda d2, label=label, href=href: g.node(d2, "generic", kids=lambda d3: g.node(d3, "link", label, " [cursor=pointer]", url=href)))
        g.node(d, "list", kids=lst)

    def banner(d):
        g.node(d, "generic", kids=lambda d1: (
            g.node(d1, "link", "Acme Docs", " [cursor=pointer]", url="/", kids=lambda d2: g.node(d2, "img", "Acme logo")),
            g.node(d1, "navigation", "Main", kids=nav),
            g.node(d1, "search", kids=lambda d2: g.node(d2, "generic", kids=lambda d3: g.node(d3, "textbox", "Search docs", "", None)))))

    def cards(d):
        for i, (t, blurb) in enumerate([("Quickstart", "Install the CLI and deploy a first app in five minutes."), ("Authentication", "API keys, OAuth and workload identity explained."),
                                         ("Webhooks", "Receive events with signed payloads and automatic retries."), ("Rate limits", "How limits are applied per key and per route.")]):
            g.node(d, "article", kids=lambda d1, t=t, blurb=blurb, i=i: g.node(d1, "generic", kids=lambda d2: (
                g.node(d2, "heading", t, " [level=3]"),
                g.node(d2, "paragraph", text=blurb),
                g.node(d2, "link", f"Read {t.lower()} guide", " [cursor=pointer]", url=f"/guides/{t.lower().replace(' ', '-')}"))))

    def main(d):
        g.node(d, "generic", kids=lambda d1: g.node(d1, "generic", kids=lambda d2: (
            g.node(d2, "heading", "Build on Acme", " [level=1]"),
            g.node(d2, "paragraph", text="Everything you need to ship and operate your app on one platform."),
            g.node(d2, "generic", kids=cards),
            g.node(d2, "heading", "What's new", " [level=2]"),
            g.node(d2, "list", kids=lambda d3: [g.node(d3, "listitem", text=f"2026.{i // 4 + 1}.{i % 4}: improved build caching and fixed webhook retries (#{4100 + i})") for i in range(changelog)]),
            g.node(d2, "button", "Show older releases", " [cursor=pointer]"))))

    def aside(d):
        g.node(d, "list", kids=lambda d1: [g.node(d1, "listitem", kids=lambda d2, i=i: g.node(d2, "link", f"Related article {i}", " [cursor=pointer]",
                    url=f"https://blog.example.com/posts/2026/03/how-we-made-builds-faster-part-{i}?utm_source=docs&utm_medium=sidebar&utm_campaign=related&utm_content=card{i}&session=9f8e7d6c5b4a")) for i in range(1, 5)])

    def footer(d):
        for col, links in (("Product", ["Features", "Pricing", "Security", "Roadmap"] + [f"Integration {i}" for i in range(extra_footer_links)]), ("Company", ["About", "Careers", "Press", "Contact"])):
            g.node(d, "generic", kids=lambda d1, col=col, links=links: (
                g.node(d1, "heading", col, " [level=4]"),
                g.node(d1, "list", kids=lambda d2: [g.node(d2, "listitem", kids=lambda d3, l=l: g.node(d3, "link", l, " [cursor=pointer]", url="/" + l.lower().replace(" ", "-"))) for l in links])))
        g.node(d, "paragraph", text="© 2026 Acme Inc. All rights reserved.")

    def page(d):
        g.node(d, "generic", kids=lambda d1: (
            g.node(d1, "banner", kids=banner),
            *([g.node(d1, "navigation", "Main", kids=nav)] if mobile_nav else []),
            g.node(d1, "main", kids=main),
            g.node(d1, "complementary", kids=aside),
            g.node(d1, "contentinfo", kids=footer),
            *([g.node(d1, "dialog", "Cookie settings", kids=lambda d2: (g.node(d2, "paragraph", text="We use cookies to improve the docs."),
                                                                          g.node(d2, "button", "Accept all", " [cursor=pointer]"), g.node(d2, "button", "Reject", " [cursor=pointer]")))] if dialog else [])))

    g.node(0, "generic", None, " [active]", kids=page)
    return g.text()


_SNAP = make_snapshot()
_SNAP_PAGE = "### Page state\n- Page URL: https://docs.example.com/\n- Page Title: Acme Docs\n- Page Snapshot:\n```yaml\n" + _SNAP + "\n```\n"
case(
    350,
    claim="Playwright-style accessibility snapshot: generic wrapper chains collapse into their child, refs are kept only on interactive or cursor=pointer nodes, '- /url:' folds into the link line (hrefs over 64 chars are cut), "
          "text-only lists beyond 6 items get a count, and a landmark that repeats an earlier one is stubbed; every interactive element (role, name, href) with its first ref, and all page content with its ancestor chain, survive.",
    loses="refs of non-interactive nodes, refs inside a stubbed duplicate landmark (its first copy keeps them), the tail of hrefs over 64 chars, and the text of list items past the sixth in lists with no links in them",
    encode=compact_a11y,
    probes=probes_350,
    fixtures=[
        Fixture("docs page: banner, main nav + mobile nav duplicate, cards, 14-bullet changelog, long tracking hrefs, footer, cookie dialog", _SNAP_PAGE, True),
        Fixture("same page without the mobile nav duplicate and with a short changelog", make_snapshot(mobile_nav=False, changelog=4), True),
        Fixture("bare snapshot, 12 extra footer links", make_snapshot(extra_footer_links=12, dialog=False), True),
        Fixture("already compact (no wrappers, no refs on content)", "- main:\n  - heading \"Hi\" [level=1]\n  - link \"Home\" [ref=e1] -> /\n", False),
        Fixture("not a snapshot", "Build succeeded in 3.2s\n- fixed 3 issues\n- updated lockfile\n", False),
        Fixture("odd indentation (left verbatim)", "- generic [ref=e1]:\n   - generic [ref=e2]:\n     - link \"x\" [ref=e3]\n", False),
        Fixture("text that looks like a stub and a 'more' node", "- list:\n  - more: +3 items omitted (no interactive elements)\n" + _SNAP[:600], None),
    ],
    min_gain=0.2,
)

# ===============================================================================================================
# #351 Digest browser network, console and Lighthouse output (shapes of Playwright MCP browser_network_requests and
# browser_console_messages, and Lighthouse JSON; all three reconstructed from memory).
# network: successful static GETs (js/css/images/fonts/dev-server modules) fold into '[static xN] js=31 css=12 ...'; every
#          failed request, every non-GET and every /api/ request stays verbatim.
# console: identical lines merge into one 'line (xN)' at the first position; no distinct message is dropped.
# lighthouse: category scores and the failing audits (id, score, displayValue) only.

_NET = re.compile(r"^\[([A-Z]+)\] (\S+) => \[(\d{3}|FAILED)\](?: (.*))?$")
_CON = re.compile(r"^\[(LOG|INFO|WARNING|WARN|ERROR|DEBUG|TRACE)\] (.*)$")
_CON_N = re.compile(r"^(.*) \(x(\d+)\)$")
_STATIC_SUM = re.compile(r"^\[static x(\d+)\]((?: [^\s=]*=\d+)+)$")
_STATIC_EXT = {"js", "mjs", "css", "png", "jpg", "jpeg", "gif", "svg", "webp", "avif", "ico", "woff", "woff2", "ttf", "otf", "map", "ts", "tsx", "jsx", "vue", "scss", "wasm"}
_API_PATH = re.compile(r"/(?:api|graphql|trpc|rpc)(?:/|$|\?)")


def _url_ext(url: str) -> str:
    path = url.split("?", 1)[0].split("#", 1)[0].rsplit("/", 1)[-1]
    return path.rsplit(".", 1)[-1].lower() if "." in path else ""


def _foldable_static(m: re.Match) -> bool:
    method, url, status = m.group(1), m.group(2), m.group(3)
    return method == "GET" and status in ("200", "304") and not _API_PATH.search(url) and (_url_ext(url) in _STATIC_EXT or "/@vite/" in url or "/@fs/" in url or "/@react-refresh" in url)


def digest_browser(text: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith("{") and '"lighthouseVersion"' in text[:4000]:
        return _lighthouse_digest(text)
    lines = text.split("\n")
    if any(l.startswith("[static x") for l in lines) or any(_CON_N.match(m.group(2)) for l in lines if (m := _CON.match(l))):
        return text  # looks like our own output already: refuse
    counts: Counter = Counter()
    first_at: dict[str, int] = {}
    for i, l in enumerate(lines):
        if _CON.match(l):
            counts[l] += 1
            first_at.setdefault(l, i)
    ext_counts: Counter = Counter()
    sum_at = None
    out: list[str] = []
    for i, l in enumerate(lines):
        m = _NET.match(l)
        if m and _foldable_static(m):
            ext_counts[_url_ext(m.group(2)) or "other"] += 1
            if sum_at is None:
                sum_at = len(out)
                out.append("")  # placeholder for the summary
            continue
        if _CON.match(l):
            if first_at[l] != i:
                continue
            if counts[l] > 1:
                l = f"{l} (x{counts[l]})"
        out.append(l)
    if sum_at is not None:
        out[sum_at] = f"[static x{sum(ext_counts.values())}]" + "".join(f" {k}={v}" for k, v in sorted(ext_counts.items(), key=lambda kv: (-kv[1], kv[0])))
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def _lighthouse_digest(text: str) -> str:
    try:
        d = json.loads(text)
        cats, audits = d["categories"], d["audits"]
        head = f"lighthouse {d['lighthouseVersion']} {d.get('finalDisplayedUrl') or d.get('finalUrl') or d['requestedUrl']}"
        if not isinstance(cats, dict) or not isinstance(audits, dict):
            return text
        lines = [head, " | ".join(f"{k} {round(v['score'] * 100)}" if v.get("score") is not None else f"{k} n/a" for k, v in cats.items())]
        for aid, a in audits.items():
            sc = a.get("score")
            if a.get("scoreDisplayMode") in ("numeric", "binary", "metricSavings") and sc is not None and sc < 0.9:
                lines.append(f"FAIL {aid} {sc:.2f} {json.dumps(a.get('title', ''))}" + (f" {json.dumps(a['displayValue'])}" if a.get("displayValue") else ""))
    except (ValueError, KeyError, TypeError, AttributeError):
        return text
    res = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return res if len(res) < len(text) else text


def probes_351(text: str) -> dict:
    """Reader view over any of the three shapes."""
    failed, apilike, ext_total, total = [], [], Counter(), 0
    errors: Counter = Counter()
    others: Counter = Counter()
    lighthouse = None
    try:
        d = json.loads(text) if text.lstrip().startswith("{") else None
    except ValueError:
        d = None
    if isinstance(d, dict) and "lighthouseVersion" in d and isinstance(d.get("categories"), dict) and isinstance(d.get("audits"), dict):
        try:
            lighthouse = {
                "url": d.get("finalDisplayedUrl") or d.get("finalUrl") or d["requestedUrl"],
                "scores": {k: (round(v["score"] * 100) if v.get("score") is not None else None) for k, v in d["categories"].items()},
                "failing": {k: (f"{a['score']:.2f}", a.get("title", ""), a.get("displayValue")) for k, a in d["audits"].items()
                            if a.get("scoreDisplayMode") in ("numeric", "binary", "metricSavings") and a.get("score") is not None and a["score"] < 0.9},
            }
        except (KeyError, TypeError, ValueError):
            lighthouse = "unreadable"
    else:
        lines = text.split("\n")
        if lines and lines[0].startswith("lighthouse ") and len(lines) >= 2:
            fails = {}
            for l in lines[2:]:
                m = re.match(r"^FAIL (\S+) (\d\.\d\d) (\"(?:[^\"\\]|\\.)*\")(?: (\".*\"))?$", l)
                if m:
                    fails[m.group(1)] = (m.group(2), json.loads(m.group(3)), json.loads(m.group(4)) if m.group(4) else None)
            scores = {}
            for part in lines[1].split(" | "):
                k, _, v = part.rpartition(" ")
                scores[k] = None if v == "n/a" else int(v)
            lighthouse = {"url": lines[0].split(" ", 2)[2], "scores": scores, "failing": fails}
        for l in lines:
            if (s := _STATIC_SUM.match(l)):
                n = int(s.group(1))
                total += n
                for kv in s.group(2).split():
                    k, v = kv.rsplit("=", 1)
                    ext_total["" if k == "other" else k] += int(v)
                continue
            if (m := _NET.match(l)):
                total += 1
                ext_total[_url_ext(m.group(2))] += 1
                if m.group(3) == "FAILED" or int(m.group(3)) >= 400:
                    failed.append(l)
                if m.group(1) != "GET" or _API_PATH.search(m.group(2)):
                    apilike.append(l)
            elif (c := _CON.match(l)):
                body, n = (_CON_N.match(l).group(1), int(_CON_N.match(l).group(2))) if _CON_N.match(l) else (l, 1)
                (errors if c.group(1) in ("ERROR", "WARNING", "WARN") else others)[body] += n
    return {
        "failed requests (4xx, 5xx, FAILED)": failed,
        "non-GET and /api/ requests": apilike,
        "request count and extension histogram": (total, sorted(ext_total.items())),
        "console errors and warnings with counts": sorted(errors.items()),
        "other console messages with counts": sorted(others.items()),
        "lighthouse scores, failing audits": lighthouse,
    }


def make_network() -> str:
    reqs = ["[GET] http://localhost:5173/ => [200] OK"]
    reqs += ["[GET] http://localhost:5173/@vite/client => [200] OK", "[GET] http://localhost:5173/@react-refresh => [200] OK", "[GET] http://localhost:5173/src/main.tsx => [200] OK"]
    mods = ["App", "Cart", "Header", "Footer", "ProductList", "ProductCard", "Checkout", "Login", "useAuth", "api", "theme", "routes", "Spinner", "Modal", "Toast", "format", "store", "hooks/useCart", "hooks/useDebounce", "pages/Home"]
    reqs += [f"[GET] http://localhost:5173/src/{m}.tsx => [200] OK" for m in mods]
    reqs += [f"[GET] http://localhost:5173/node_modules/.vite/deps/{d}.js?v=a1b2c3d4 => [200] OK" for d in ["react", "react-dom_client", "react-router-dom", "zustand", "axios", "clsx", "date-fns", "chunk-4X7Y2Z"]]
    reqs += ["[GET] http://localhost:5173/src/index.css => [200] OK", "[GET] http://localhost:5173/src/App.css => [200] OK"]
    reqs += [f"[GET] http://localhost:5173/assets/img/product-{i}.jpg => [200] OK" for i in range(1, 19)]
    reqs += [f"[GET] https://fonts.gstatic.com/s/inter/v13/font{i}.woff2 => [200] OK" for i in range(3)]
    reqs.insert(9, "[GET] http://localhost:5173/api/products?page=1 => [200] OK")
    reqs.insert(24, "[POST] http://localhost:5173/api/login => [401] Unauthorized")
    reqs.insert(40, "[GET] http://localhost:5173/favicon.ico => [404] Not Found")
    reqs.insert(41, "[GET] https://fonts.gstatic.com/s/inter/v13/font9.woff2 => [FAILED] net::ERR_CONNECTION_REFUSED")
    reqs += ["[GET] http://localhost:5173/api/cart => [500] Internal Server Error", "[GET] http://localhost:5173/assets/logo.svg => [304] Not Modified"]
    return "### Result\n" + "\n".join(reqs) + "\n"


def make_console() -> str:
    rows = ["[ERROR] TypeError: Cannot read properties of undefined (reading 'map') @ http://localhost:5173/src/Cart.tsx:42",
            "[LOG] [vite] connecting... @ http://localhost:5173/@vite/client:742", "[LOG] [vite] connected. @ http://localhost:5173/@vite/client:845",
            "[INFO] %cDownload the React DevTools for a better development experience @ http://localhost:5173/node_modules/.vite/deps/react-dom_client.js:20102"]
    for i in range(6):
        rows.append("[WARNING] ⚠️ React Router Future Flag Warning: v7_startTransition @ http://localhost:5173/node_modules/.vite/deps/react-router-dom.js:4392")
        rows.append("[LOG] [vite] hot updated: /src/App.tsx @ http://localhost:5173/@vite/client:223")
        if i % 2:
            rows.append("[LOG] [vite] hot updated: /src/Cart.tsx @ http://localhost:5173/@vite/client:223")
    rows += ["[ERROR] Failed to load resource: the server responded with a status of 401 (Unauthorized) @ http://localhost:5173/api/login:0"] * 3
    rows += ["[LOG] [vite] hot updated: /src/App.tsx @ http://localhost:5173/@vite/client:223", "[ERROR] Uncaught (in promise) AxiosError: Request failed with status code 500 @ http://localhost:5173/src/api.ts:18"]
    return "\n".join(rows) + "\n"


def make_lighthouse() -> str:
    cats = {"performance": 0.62, "accessibility": 0.94, "best-practices": 1.0, "seo": 0.82}
    audits = {}
    long_desc = "Lighthouse measures how the page loads and renders. This audit checks a specific behaviour and links to the documentation for fixes. [Learn more about this audit](https://developer.chrome.com/docs/lighthouse/performance/audit-name/). "
    names = ["first-contentful-paint", "largest-contentful-paint", "total-blocking-time", "cumulative-layout-shift", "speed-index", "interactive", "render-blocking-resources", "unused-javascript", "unused-css-rules",
             "uses-optimized-images", "uses-text-compression", "uses-responsive-images", "offscreen-images", "font-display", "dom-size", "redirects", "uses-http2", "mainthread-work-breakdown", "bootup-time",
             "color-contrast", "image-alt", "label", "link-name", "document-title", "html-has-lang", "meta-viewport", "heading-order", "tap-targets", "viewport", "meta-description", "http-status-code", "is-crawlable",
             "canonical", "robots-txt", "font-size", "hreflang", "uses-passive-event-listeners", "no-document-write", "js-libraries", "geolocation-on-start", "errors-in-console", "inspector-issues", "image-aspect-ratio"]
    failing = {"largest-contentful-paint": (0.12, "4.2 s"), "total-blocking-time": (0.45, "610 ms"), "render-blocking-resources": (0.5, "Potential savings of 420 ms"), "color-contrast": (0.0, None),
               "meta-description": (0.0, None), "errors-in-console": (0.0, None)}
    for i, n in enumerate(names):
        sc, dv = failing.get(n, (1.0, None))
        a = {"id": n, "title": n.replace("-", " ").capitalize(), "description": long_desc * 2, "score": sc, "scoreDisplayMode": "numeric" if i < 18 else "binary", "numericValue": 1234.5 + i,
             "numericUnit": "millisecond", "details": {"type": "table", "headings": [{"key": "url", "valueType": "url", "label": "URL"}, {"key": "wastedMs", "valueType": "timespanMs", "label": "Potential Savings"}],
                                                       "items": [{"url": f"http://localhost:5173/assets/chunk-{j}.js", "totalBytes": 10240 * (j + 1), "wastedMs": 12.5 * j} for j in range(4)], "overallSavingsMs": 420}}
        if dv:
            a["displayValue"] = dv
        audits[n] = a
    d = {"lighthouseVersion": "11.7.1", "requestedUrl": "http://localhost:5173/", "mainDocumentUrl": "http://localhost:5173/", "finalUrl": "http://localhost:5173/", "finalDisplayedUrl": "http://localhost:5173/",
         "userAgent": "Mozilla/5.0 Chrome-Lighthouse", "fetchTime": "2026-03-02T10:15:30.123Z", "environment": {"networkUserAgent": "x", "hostUserAgent": "x", "benchmarkIndex": 2100},
         "runWarnings": [], "configSettings": {"output": ["json"], "maxWaitForLoad": 45000, "formFactor": "mobile", "throttlingMethod": "simulate", "onlyCategories": ["performance", "accessibility", "best-practices", "seo"]},
         "audits": audits, "categories": {k: {"title": k.title(), "id": k, "score": v, "auditRefs": [{"id": n, "weight": 1, "group": "metrics"} for n in names[:12]]} for k, v in cats.items()},
         "timing": {"entries": [{"startTime": 100.0 + i, "name": f"lh:runner:{i}", "duration": 3.5, "entryType": "measure"} for i in range(30)], "total": 9500.2},
         "i18n": {"icuMessagePaths": {f"core/audits/{n}.js | title": [f"audits[{n}].title"] for n in names}}}
    return json.dumps(d, indent=2) + "\n"


_NET_TXT = make_network()
case(
    351,
    claim="Playwright-style network lists fold successful static GETs into one '[static xN] ext=count' line (every failed request, non-GET and /api/ request stays verbatim); console lists merge identical lines into 'line (xN)'; "
          "Lighthouse JSON becomes the category scores plus the failing audits; the request total and extension histogram, every 4xx/5xx/FAILED request, every error and warning with its count, and all scores and failing audits are unchanged.",
    loses="which static asset was which (only the extension histogram stays), the position of repeated console lines after the first, and every passing Lighthouse audit with all descriptions, details, i18n strings and timings",
    encode=digest_browser,
    probes=probes_351,
    fixtures=[
        Fixture("network list of a Vite dev page (401, 404, FAILED and 500 among 70 assets)", _NET_TXT, True),
        Fixture("console: HMR noise, repeated warning, errors first, middle and last", make_console(), True),
        Fixture("network list followed by console in one tool result", _NET_TXT + "\n### Console\n" + make_console(), True),
        Fixture("Lighthouse JSON, 43 audits, 6 failing", make_lighthouse(), True),
        Fixture("network list that is only failures", "### Result\n[GET] http://localhost:5173/api/a => [500] Internal Server Error\n[GET] http://localhost:5173/app.js => [404] Not Found\n[POST] http://localhost:5173/api/b => [200] OK\n", False),
        Fixture("console with all-distinct lines", "[LOG] a @ x:1\n[ERROR] b @ x:2\n[WARNING] c @ x:3\n", False),
        Fixture("json that is not lighthouse", json.dumps({"audits": {"a": 1}, "categories": []}), False),
        Fixture("console line that already ends in (x3)", "[ERROR] boom (x3) @ a.js:1\n[ERROR] boom (x3) @ a.js:1\n[LOG] [static x2] js=2\n", None),
    ],
    min_gain=0.3,
)

# ===============================================================================================================
# #384 Strip corpus-wide boilerplate: a line present in more than 20% of the documents of a crawled / PDF corpus is removed
# at ingestion. Corpus-level, so the 'input' is a list of documents serialised as '=== doc: ID ===' blocks. A document's title
# (its first non-blank line) is protected, lines under 8 characters are never counted, and running headers that differ only
# in a page number share one key.

_DOC_HDR = re.compile(r"^=== doc: (.+) ===$")
_PAGE_NO = re.compile(r"\b(?:page|p\.)\s*\d+(?:\s*(?:of|/)\s*\d+)?\b", re.I)
DF_PERCENT = 20
MIN_DOCS = 5


def _bp_key(line: str) -> str | None:
    s = " ".join(line.split())
    if len(s) < 8:
        return None
    return _PAGE_NO.sub("page #", s) if _PAGE_NO.search(s) else s


def _keys(body: list[str]) -> list[str | None]:
    """Per line: its frequency key, or None when the line is never counted or stripped (short, a code fence, or inside a fenced code block)."""
    out: list[str | None] = []
    inside = False
    for l in body:
        if l.strip().startswith("```"):
            inside = not inside
            out.append(None)
        else:
            out.append(None if inside else _bp_key(l))
    return out


def _split_docs(text: str):
    """-> (preamble lines, [(header line, body lines)])."""
    pre: list[str] = []
    docs: list[tuple[str, list[str]]] = []
    for l in text.split("\n"):
        if _DOC_HDR.match(l):
            docs.append((l, []))
        elif docs:
            docs[-1][1].append(l)
        else:
            pre.append(l)
    return pre, docs


def strip_boilerplate(text: str) -> str:
    pre, docs = _split_docs(text)
    if len(docs) < MIN_DOCS:
        return text
    df: Counter = Counter()
    for _, body in docs:
        df.update({k for k in _keys(body) if k is not None})
    bad = {k for k, n in df.items() if n * 100 > DF_PERCENT * len(docs)}
    if not bad:
        return text
    out = list(pre)
    for hdr, body in docs:
        out.append(hdr)
        title = next((i for i, l in enumerate(body) if l.strip()), None)
        out += [l for i, (l, k) in enumerate(zip(body, _keys(body))) if i == title or k is None or k not in bad]
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def probes_384(text: str) -> dict:
    """Reader view: document ids, each document's title, and every line that is not corpus boilerplate, in place."""
    pre, docs = _split_docs(text)
    n = len(docs)
    freq: dict[str, set[int]] = defaultdict(set)
    keyed = []
    for i, (_, body) in enumerate(docs):
        ks = _keys(body)
        keyed.append(ks)
        for k in ks:
            if k is not None:
                freq[k].add(i)
    boiler = {k for k, s in freq.items() if n >= MIN_DOCS and len(s) * 5 > n}
    kept = []
    for i, (hdr, body) in enumerate(docs):
        nb = [(l, k) for l, k in zip(body, keyed[i]) if l.strip()]
        kept.append((_DOC_HDR.match(hdr).group(1), nb[0][0] if nb else None, [l for l, k in nb[1:] if k is None or k not in boiler]))
    return {"preamble": pre, "documents (id, title, non-boilerplate lines)": kept}


def make_corpus(n: int = 30, seed: int = 7) -> str:
    rnd = random.Random(seed)
    topics = ["authentication", "webhooks", "pagination", "rate limits", "retries", "idempotency", "file uploads", "search", "exports", "audit logs", "roles", "billing", "sandbox", "errors", "versioning"]
    verbs = ["returns", "accepts", "rejects", "queues", "retries", "signs", "paginates", "truncates"]
    out = []
    for i in range(n):
        t = topics[i % len(topics)]
        out.append(f"=== doc: https://docs.example.com/{t.replace(' ', '-')}/v{i // len(topics) + 1} ===")
        out.append(f"# {t.title()} (v{i // len(topics) + 1})")
        if i % 10 != 9:
            out += ["We use cookies to improve your experience. Accept all cookies?", "Manage cookie settings"]
        out += ["Skip to main content", "Docs | Guides | API reference | Changelog | Status | Support", "Sign in    Start for free"]
        for j in range(rnd.randint(2, 4)):
            out.append(f"The {t} endpoint {rnd.choice(verbs)} up to {rnd.randint(10, 900)} items per request and {rnd.choice(verbs)} the rest after {rnd.randint(2, 60)} seconds (case {i}-{j}).")
            out.append("")
        if i % 4 == 0:
            out += ["```python", "import acme_sdk as acme", f"acme.{t.replace(' ', '_')}.call(limit={i + 5})", "```"]
        if i % 5 == 1:
            out.append("Deprecated since 2.0: use the batch endpoint instead.")  # 6 of 30 = exactly 20%: kept
        if i % 7 in (0, 3):
            out.append("Related: Getting started | Concepts | Tutorials | Troubleshooting")  # 9 of 30 = 30%: boilerplate
        out += ["", "Was this page helpful?  Yes / No", "© 2026 Acme Inc. All rights reserved. Licensed under CC-BY-4.0.", "Privacy policy | Terms of service | Cookie settings", ""]
    return "\n".join(out)


def make_pdf_corpus() -> str:
    rnd = random.Random(11)
    out = []
    for i in range(8):
        out.append(f"=== doc: report-{i}.pdf ===")
        out.append(f"Annual Report Section {i + 1}")
        for p in range(1, 4):
            out += [f"ACME Corp Confidential - Page {p} of 3", f"Revenue in region {i}-{p} grew by {rnd.randint(2, 19)}.{rnd.randint(0, 9)} percent against {rnd.randint(1000, 9999)} tickets.", ""]
    return "\n".join(out)


_CORPUS = make_corpus()
case(
    384,
    claim="In a serialised corpus of at least 5 documents, every line (8+ characters, page numbers masked) that occurs in more than 20% of the documents is removed from all of them; each document's title, every line at or below the threshold "
          "(including one at exactly 20%) and the document order are unchanged.",
    loses="legitimate prose that happens to repeat in more than 20% of the documents (a shared definition, a standard warning) is removed from every document; fenced code blocks and each document's title are never touched",
    encode=strip_boilerplate,
    probes=probes_384,
    fixtures=[
        Fixture("30 crawled doc pages: nav, cookie banner, footer, 30% related links, 20% deprecation note", _CORPUS, True),
        Fixture("8 PDF reports with a running header carrying page numbers", make_pdf_corpus(), True),
        Fixture("corpus of 4 documents (too small for a frequency)", "\n".join(_CORPUS.split("\n")[: next(i for i, l in enumerate(_CORPUS.split("\n")) if l.startswith("=== doc: ") and "/v1 ===" in l and "retries" in l)]) + "\n", False),
        Fixture("no document markers at all", "line one is long enough\nline one is long enough\nline one is long enough\n", False),
        Fixture("documents with nothing in common", "\n".join(f"=== doc: d{i} ===\n# Title {i}\nunique content number {i} about topic {i * 7}\n" for i in range(8)), False),
        Fixture("title that repeats in every document is protected", "\n".join(f"=== doc: d{i} ===\nOverview of the platform\nunique text number {i} here\nshared footer line for everyone\n" for i in range(8)), True),
    ],
    min_gain=0.1,
)

# ===============================================================================================================
# #394 Collapse vendored and ignored subtrees in find, ls -R and du -a listings.
# A path component in VENDORED starts a vendored root (the path up to and including it). find: the root and everything below it
# become 'ROOT/ (N paths)' (N = lines strictly below the root); du -a: descendants are dropped and the root's own line gets
# ' (N paths)'; ls -R: the sections of the root and below become 'ROOT/ (N entries)'. A root is folded only when the listing
# also shows something outside it (so 'find node_modules/x' is left alone) and it has at least 2 lines below it. The listing
# kind is recognised strictly (every line must fit), so a mutated or mixed listing is never half-folded.

VENDORED = {"node_modules", ".venv", "venv", "site-packages", "dist", "target", "__pycache__", ".git", ".tox", ".mypy_cache", ".pytest_cache", ".next"}
_FOLD_FIND = re.compile(r"^(.+)/ \((\d+) paths\)$")
_FOLD_DU = re.compile(r"^([^\t]+)\t(.+?) \((\d+) paths\)$")
_FOLD_LS = re.compile(r"^(.+)/ \((\d+) entries\)$")
_DU_LINE = re.compile(r"^([0-9][0-9.,]*[KMGTP]?)\t(.+)$")


def _vroot(path: str) -> str | None:
    """Path up to and including the first vendored component, or None."""
    parts = path.split("/")
    for i, p in enumerate(parts):
        if p in VENDORED:
            return "/".join(parts[: i + 1])
    return None


def _ls_paras(body: str):
    """-> list of paragraphs (list of lines) if body is a clean ls -R sequence of sections, else None."""
    paras = [p.split("\n") for p in body.split("\n\n")]
    for p in paras:
        if not p or p[0] == "" or "" in p:
            return None
        if not (p[0].endswith(":") or (len(p) == 1 and _FOLD_LS.match(p[0]))):
            return None
    return paras


def _kind(text: str) -> str:
    trail = text[:-1] if text.endswith("\n") else text
    lines = trail.split("\n")
    if not trail:
        return ""
    if all(_DU_LINE.match(l) for l in lines):
        return "du"
    if "\t" not in trail and "\n\n" not in trail and all(l == "." or "/" in l for l in lines) and "" not in lines:
        return "find"
    if "\t" not in trail and _ls_paras(trail) is not None and any(l.endswith(":") for l in lines):
        return "ls"
    return ""


def _alone(roots: list) -> list:
    """Drop a root that covers the whole listing (the user asked to look inside it)."""
    for r in {x for x in roots if x}:
        if all(x == r for x in roots if x is not None) and all(x is not None for x in roots):
            roots = [None if x == r else x for x in roots]
    return roots


def fold_listing(text: str) -> str:
    kind = _kind(text)
    trail = text.endswith("\n")
    body = text[:-1] if trail else text
    if not kind or any(_FOLD_FIND.match(l) or _FOLD_DU.match(l) or _FOLD_LS.match(l) for l in body.split("\n")):
        return text
    out: list[str] = []
    if kind == "find":
        lines = body.split("\n")
        roots = _alone([_vroot(l) for l in lines])
        below = Counter(r for l, r in zip(lines, roots) if r and l != r)
        done: set[str] = set()
        for l, r in zip(lines, roots):
            if r is None or below[r] < 2:
                out.append(l)
            elif r not in done:
                done.add(r)
                out.append(f"{r}/ ({below[r]} paths)")
        res = "\n".join(out)
    elif kind == "du":
        lines = body.split("\n")
        paths = [_DU_LINE.match(l).group(2) for l in lines]
        roots = _alone([_vroot(p) for p in paths])
        below = Counter(r for p, r in zip(paths, roots) if r and p != r)
        has_root = {p for p, r in zip(paths, roots) if r and p == r}
        for l, p, r in zip(lines, paths, roots):
            if r is None or below[r] < 2 or r not in has_root:
                out.append(l)
            elif p == r:
                out.append(f"{l} ({below[r]} paths)")
        res = "\n".join(out)
    else:
        paras = _ls_paras(body)
        roots = _alone([_vroot(p[0][:-1]) for p in paras])
        entries = Counter()
        for p, r in zip(paras, roots):
            if r:
                entries[r] += len(p) - 1
        done = set()
        for p, r in zip(paras, roots):
            if r is None or entries[r] < 2:
                out.append("\n".join(p))
            elif r not in done:
                done.add(r)
                out.append(f"{r}/ ({entries[r]} entries)")
        res = "\n\n".join(out)
    res += "\n" if trail else ""
    return res if len(res) < len(text) else text


def probes_394(text: str) -> dict:
    """Reader view per listing kind: every line / section outside vendored roots verbatim, each vendored root's size (du) and how many lines or entries sit below it."""
    kind = _kind(text)
    body = text[:-1] if text.endswith("\n") else text
    plain: list = []
    below: Counter = Counter()
    sizes: dict = {}
    if kind in ("find", "du"):
        for l in body.split("\n"):
            size, p = (l.split("\t", 1) if kind == "du" else (None, l))
            if kind == "du" and (m := _FOLD_DU.match(l)):
                sizes[m.group(2)] = m.group(1)
                below[m.group(2)] += int(m.group(3))
                continue
            if kind == "find" and (m := _FOLD_FIND.match(l)):
                below[m.group(1)] += int(m.group(2))
                continue
            comps = p.split("/")
            idx = next((i for i, c in enumerate(comps) if c in VENDORED), None)
            if idx is None:
                plain.append(l)
            elif idx == len(comps) - 1:
                if kind == "du":
                    sizes["/".join(comps)] = size
            else:
                below["/".join(comps[: idx + 1])] += 1
    elif kind == "ls":
        for para in body.split("\n\n"):
            lines = para.split("\n")
            if len(lines) == 1 and (m := _FOLD_LS.match(lines[0])):
                below[m.group(1)] += int(m.group(2))
                continue
            header = lines[0][:-1]
            comps = header.split("/")
            idx = next((i for i, c in enumerate(comps) if c in VENDORED), None)
            if idx is None:
                plain.append((header, tuple(lines[1:])))
            else:
                below["/".join(comps[: idx + 1])] += len(lines) - 1
    else:
        plain = body.split("\n")
    return {"kind": kind, "lines or sections outside vendored roots": plain, "vendored roots: entries below": sorted(below.items()), "du root sizes": sorted(sizes.items())}


def make_tree() -> list[str]:
    """Deterministic repo file list (relative paths, no './'): a JS + Python project with a node_modules, a .venv and a dist."""
    f = ["README.md", "package.json", "pyproject.toml", "setup.cfg", ".gitignore", "Makefile", "docs/index.md", "docs/guide.md", "docs/api.md", "scripts/build.sh", "scripts/release.sh",
         "src/app/__init__.py", "src/app/main.py", "src/app/models.py", "src/app/views.py", "src/app/util.py", "src/web/index.ts", "src/web/App.tsx", "src/web/components/Button.tsx",
         "src/web/components/Table.tsx", "src/distribution/pack.py", "tests/test_main.py", "tests/test_views.py", "tests/conftest.py", "node_modules.md"]
    f += [f"src/app/__pycache__/{m}.cpython-313.pyc" for m in ("__init__", "main", "models", "views", "util")]
    for p in ["left-pad", "lodash", "react", "react-dom", "scheduler", "typescript", "webpack", "babel-core", "chalk", "debug", "express", "ms", "qs", "semver", "uuid", "yargs"]:
        f += [f"node_modules/{p}/package.json", f"node_modules/{p}/index.js", f"node_modules/{p}/README.md", f"node_modules/{p}/lib/main.js", f"node_modules/{p}/lib/util.js", f"node_modules/{p}/lib/cjs/index.js"]
    f += [f"node_modules/@types/node/{x}.d.ts" for x in ("index", "fs", "path", "http", "stream", "events")]
    for p in ["requests", "urllib3", "certifi", "idna", "flask", "jinja2", "click", "werkzeug", "pytest", "numpy", "pydantic", "sqlalchemy"]:
        f += [f".venv/lib/python3.13/site-packages/{p}/__init__.py", f".venv/lib/python3.13/site-packages/{p}/core.py", f".venv/lib/python3.13/site-packages/{p}/utils.py", f".venv/lib/python3.13/site-packages/{p}/_compat.py",
              f".venv/lib/python3.13/site-packages/{p}-1.0.0.dist-info/METADATA", f".venv/lib/python3.13/site-packages/{p}-1.0.0.dist-info/RECORD"]
    f += [".venv/pyvenv.cfg", ".venv/bin/python", ".venv/bin/pip"]
    f += [f"dist/assets/chunk-{i}.js" for i in range(12)] + ["dist/index.html", "dist/bundle.js", "dist/bundle.js.map"]
    return f


def _dirs_files(files: list[str]):
    dirs: dict[str, list[str]] = {"": []}
    for p in sorted(files):
        parts = p.split("/")
        for i in range(len(parts)):
            d, name = "/".join(parts[:i]), parts[i]
            full = "/".join(parts[: i + 1])
            if full not in dirs.setdefault(d, []) and name not in dirs[d]:
                dirs[d].append(name)
            if i < len(parts) - 1:
                dirs.setdefault(full, [])
    return dirs


def listing_find(files: list[str]) -> str:
    dirs = _dirs_files(files)
    out = ["."]

    def walk(d: str):
        for n in sorted(dirs.get(d, [])):
            full = f"{d}/{n}" if d else n
            out.append("./" + full)
            if full in dirs:
                walk(full)
    walk("")
    return "\n".join(out) + "\n"


def listing_du(files: list[str]) -> str:
    dirs = _dirs_files(files)
    out: list[str] = []

    def size(path: str) -> int:
        return 4 * (1 + sum(ord(c) for c in path) % 40)

    def walk(d: str) -> int:
        total = 4
        for n in sorted(dirs.get(d, [])):
            full = f"{d}/{n}" if d else n
            if full in dirs:
                total += walk(full)
            else:
                s = size(full)
                out.append(f"{s}\t./{full}")
                total += s
        out.append(f"{total}\t./{d}" if d else f"{total}\t.")
        return total
    walk("")
    return "\n".join(out) + "\n"


def listing_ls(files: list[str]) -> str:
    dirs = _dirs_files(files)
    out: list[str] = []

    def walk(d: str):
        out.append(("./" + d if d else ".") + ":\n" + "\n".join(sorted(dirs.get(d, []))))
        for n in sorted(dirs.get(d, [])):
            full = f"{d}/{n}" if d else n
            if full in dirs:
                walk(full)
    walk("")
    return "\n\n".join(out) + "\n"


_FILES = make_tree()
_FIND, _DU, _LS = listing_find(_FILES), listing_du(_FILES), listing_ls(_FILES)
case(
    394,
    claim="In find, du -a and ls -R listings, a vendored root (node_modules, .venv, site-packages, dist, target, __pycache__, .git, ...) that has 2+ lines below it and is not the whole listing becomes 'ROOT/ (N paths)' "
          "(du: the root's own size line gets the count); every line or section outside vendored roots is byte-identical, and each root's size and count are unchanged.",
    loses="the names of everything below a vendored root (only the count of lines or entries survives), and the distinction between a root that was itself listed and one that was not",
    encode=fold_listing,
    probes=probes_394,
    fixtures=[
        Fixture("find . in a JS + Python repo (node_modules, .venv, dist, __pycache__)", _FIND, True),
        Fixture("du -a of the same repo (post-order, size per line)", _DU, True),
        Fixture("ls -Ra of the same repo", _LS, True),
        Fixture("find with the source files first, vendored tree last", "\n".join([l for l in _FIND.split("\n") if l and not any(f"/{v}" in l for v in ("node_modules/", ".venv/"))] + [l for l in _FIND.split("\n") if "node_modules/" in l]) + "\n", True),
        Fixture("find inside node_modules (the whole listing is the vendored root)", "\n".join(["./node_modules/left-pad/index.js", "./node_modules/left-pad/lib/main.js", "./node_modules/left-pad/lib/util.js"]) + "\n", False),
        Fixture("find of source only", "\n".join(["."] + [f"./{p}" for p in _FILES if p.startswith(("src/", "tests/")) and "__pycache__" not in p]) + "\n", False),
        Fixture("find . -name node_modules -type d (roots without descendants)", "./node_modules\n./packages/a/node_modules\n./packages/b/node_modules\n", False),
        Fixture("not a listing", "Compiling core v0.4.2\nwarning: unused import: `std::fmt`\n", False),
        Fixture("listing that already contains a fold line", _FIND.replace("./dist", "./dist/ (3 paths)", 1), None),
    ],
    min_gain=0.5,
)

# ===============================================================================================================
# #396 Strip quoted replies and signatures from an email thread rendered as '--- Message N ---' blocks (header lines, blank
# line, body). A '>'-quoted block (with the 'On ... wrote:' line above it) is replaced by '[quote omitted: N lines]' when it
# has 2+ content lines and every one of them already appeared in an earlier message (own text, or a quote that was kept);
# the quoted signature inside it does not count. The '-- ' signature (up to the next quote or attribution) and
# 'Sent from my ...' lines are dropped. A message that replies inline (own text between two quote blocks) is left alone.

_MSG_SEP = re.compile(r"^--- Message (\d+) ---$")
_ATTR = re.compile(r"^On .{5,120} wrote:$")
_SENT = re.compile(r"^(Sent from my (iPhone|iPad|Android|Galaxy)|Get Outlook for (iOS|Android))\b")
_QUOTE = re.compile(r"^((?:>[ \t]?)+)(.*)$")
_OMIT = re.compile(r"^\[quote omitted: \d+ lines\]$")


def _qparse(line: str):
    m = _QUOTE.match(line)
    return m.group(1).count(">"), m.group(2).strip()


def _quote_content(block: list[str]) -> list[str]:
    """Normalised content lines of a quote block: blank lines, quoted attributions and quoted signatures removed."""
    out, skip = [], None
    for raw in block:
        depth, text = _qparse(raw)
        if text.rstrip() == "--":
            skip = depth
            continue
        if skip is not None:
            if depth == skip and not _ATTR.match(text):
                continue
            skip = None
        if text and not _ATTR.match(text) and not _SENT.match(text):
            out.append(" ".join(text.split()))
    return out


def strip_thread(text: str) -> str:
    lines = text.split("\n")
    starts = [i for i, l in enumerate(lines) if _MSG_SEP.match(l)]
    if not starts:
        return text
    out = lines[: starts[0]]
    seen: set[str] = set()
    for k, s in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        seg = lines[s:end]
        h = next((j for j, l in enumerate(seg) if l == ""), len(seg))
        out += seg[: h + 1]
        body = seg[h + 1:]
        # classify
        items: list[tuple[str, str]] = []  # (kind, line)
        in_sig = False
        for l in body:
            if l.startswith(">"):
                in_sig = False
                items.append(("quote", l))
            elif _ATTR.match(l):
                in_sig = False
                items.append(("attr", l))
            elif in_sig:
                items.append(("sig", l))
            elif l in ("-- ", "--"):
                in_sig = True
                items.append(("sig", l))
            elif _SENT.match(l):
                items.append(("sig", l))
            else:
                items.append(("own", l))
        # quote blocks = runs of 'quote' items, with a directly preceding 'attr'
        blocks, i = [], 0
        while i < len(items):
            if items[i][0] == "quote":
                j = i
                while j < len(items) and items[j][0] == "quote":
                    j += 1
                a = i - 1 if i > 0 and items[i - 1][0] == "attr" else i
                blocks.append((a, i, j))
                i = j
            else:
                i += 1
        inline = False
        if len(blocks) >= 2:
            inline = any(items[x][0] == "own" and items[x][1].strip() for x in range(blocks[0][2], blocks[-1][1]))
        drop: dict[int, tuple[int, int]] = {}
        for a, i, j in blocks:
            content = _quote_content([l for _, l in items[i:j]])
            if not inline and len(content) >= 2 and all(c in seen for c in content):
                drop[a] = (j, j - i)
        res: list[str] = []
        x = 0
        while x < len(items):
            if x in drop:
                res.append(f"[quote omitted: {drop[x][1]} lines]")
                x = drop[x][0]
                continue
            if items[x][0] != "sig":
                res.append(items[x][1])
            x += 1
        out += res
        # update what has been seen (kept own text and kept quotes)
        kept_quote = [l for kind, l in items if kind == "quote"]
        for kind, l in items:
            if kind == "own" and l.strip():
                seen.add(" ".join(l.split()))
        for a, i, j in blocks:
            if a not in drop:
                seen.update(_quote_content([l for _, l in items[i:j]]))
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def probes_396(text: str) -> dict:
    """State-machine reader: per message the header triple, own text lines, the quoted lines that had not appeared earlier; plus the set of all distinct content lines."""
    lines = text.split("\n")
    pre: list[str] = []
    msgs: list[list[str]] = []
    for l in lines:
        if _MSG_SEP.match(l):
            msgs.append([])
        elif msgs:
            msgs[-1].append(l)
        else:
            pre.append(l)
    seen: set[str] = set()
    heads, owns, unfound = [], [], []
    for n, m in enumerate(msgs):
        zone = m[: m.index("")] if "" in m else m
        body = m[m.index("") + 1:] if "" in m else []
        heads.append(tuple(next((z for z in zone if z.startswith(p)), None) for p in ("From: ", "Date: ", "Subject: ")))
        own: list[str] = []
        new_q: list[str] = []
        all_new: set[str] = set()
        in_sig, skip = False, None
        for l in body:
            if l.startswith(">"):
                in_sig = False
                depth, text_ = _qparse(l)
                if text_.rstrip() == "--":
                    skip = depth
                    continue
                if skip is not None:
                    if depth == skip and not _ATTR.match(text_):
                        continue
                    skip = None
                if text_ and not _ATTR.match(text_) and not _SENT.match(text_):
                    t = " ".join(text_.split())
                    all_new.add(t)
                    if t not in seen:
                        new_q.append(t)
                continue
            skip = None
            if _OMIT.match(l) or _ATTR.match(l):
                in_sig = False
                continue
            if in_sig:
                continue
            if l in ("-- ", "--"):
                in_sig = True
                continue
            if _SENT.match(l):
                continue
            if l.strip():
                own.append(l)
        owns.append(tuple(own))
        unfound.append(tuple(new_q))
        seen |= {" ".join(o.split()) for o in own} | all_new
    return {
        "preamble": pre,
        "message headers (From, Date, Subject)": heads,
        "own text of each message": owns,
        "quoted lines not seen in an earlier message": unfound,
        "distinct content lines of the thread": sorted(seen),
    }


_PEOPLE = [("Alice Chen", "alice@acme.com", "VP Product", "+31 20 555 0101"), ("Bob Marsh", "bob@acme.com", "CTO", "+31 20 555 0102"), ("Dana Ortiz", "dana@partner.io", "Finance Lead", "+1 415 555 0133")]
_DATES = ["Mon, 2 Mar 2026 09:14", "Mon, 2 Mar 2026 11:40", "Tue, 3 Mar 2026 08:05", "Tue, 3 Mar 2026 16:22", "Wed, 4 Mar 2026 10:01", "Thu, 5 Mar 2026 09:48", "Fri, 6 Mar 2026 13:30"]
_OWN = [
    ["Hi all,", "I propose we raise the Team plan from 19 to 24 euro per seat starting 1 June.", "The Decision we need this week: pricing change goes ahead or we wait for Q4.", "Please reply with objections by Wednesday."],
    ["Thanks Alice. Engineering can support the new price tiers by 15 May.", "The billing migration is the long pole, about three weeks of work for two people.", "I would not start before the invoice table cleanup is done."],
    ["Finance here. At 24 euro the forecast adds roughly 310k to Q3 revenue.", "Churn risk is the open question: the last increase cost us 2.1 percent of seats.", "Approved by Dana on March 9 provided we grandfather existing annual plans."],
    ["Agreed on grandfathering annual plans until their renewal date.", "Alice, can you draft the customer email? Legal should see it before it goes out."],
    ["Draft attached in the shared folder. Legal review is booked for Monday.", "I changed the start date to 15 June so the email lands two weeks ahead."],
    ["Final: budget for the migration is capped at 40k, signed off by Dana.", "Rollout date stays 15 June; Bob owns the cutover checklist."],
]


def make_thread(n: int = 6, inline_at: int | None = None, mobile: bool = False) -> str:
    full: list[list[str]] = []  # rendered body of each message
    out = ["Thread: Q3 pricing change", ""]
    for i in range(n):
        who = _PEOPLE[i % 3]
        sig = ["-- ", f"{who[0]} | {who[2]}", f"Acme Inc | {who[3]}", "", "This email and any attachments are confidential and intended solely for the addressee."]
        body = list(_OWN[i])
        if mobile and i == 3:
            body = body + ["Sent from my iPhone"]
        if i > 0:
            prev, pw = full[i - 1], _PEOPLE[(i - 1) % 3]
            attr = f"On {_DATES[i - 1]}, {pw[0]} <{pw[1]}> wrote:"
            if inline_at == i:
                q = ["> " + l if l else ">" for l in prev[:4]]
                body = [q[0], body[0], q[1], body[1] if len(body) > 1 else "ok", q[2], "Agreed with the rest."] + [attr] + q[3:]
                body = [attr] + body[:0] + body
            else:
                body = body + [""] + sig + [""] + [attr] + [("> " + l if l else ">") if not l.startswith(">") else ">" + l for l in prev]
        else:
            body = body + [""] + sig
        full.append(body)
        out += [f"--- Message {i + 1} ---", f"From: {who[0]} <{who[1]}>", f"Date: {_DATES[i]} +0100", "Subject: " + ("Re: " if i else "") + "Q3 pricing change", ""] + body + [""]
    return "\n".join(out)


_THREAD = make_thread()
case(
    396,
    claim="In a rendered email thread, a '>' quote block whose content lines (quoted signatures ignored) all appeared in earlier messages is replaced by '[quote omitted: N lines]', '-- ' signatures and 'Sent from my ...' lines are dropped; "
          "every message header, every line of own text (who decided what, when), every quoted line that was NOT seen earlier, and the set of distinct content lines of the thread are unchanged.",
    loses="the 'On <date> <name> wrote:' attribution lines of dropped quotes, signature blocks and legal footers (names, titles, phones), and the exact quote markup of repeated text",
    encode=strip_thread,
    probes=probes_396,
    fixtures=[
        Fixture("6-message thread, each reply quoting everything above (decisions in messages 1, 3 and 6)", _THREAD, True),
        Fixture("same with a mobile signature in message 4", make_thread(mobile=True), True),
        Fixture("3-message thread", make_thread(3), True),
        Fixture("inline replies in message 3 (own text between quote lines): left alone for that message", make_thread(inline_at=2), True),
        Fixture("single message without signature or quote", "Thread: x\n\n--- Message 1 ---\nFrom: A <a@x.com>\nDate: d\nSubject: s\n\nPlease approve the budget.\nThanks.\n", False),
        Fixture("quote of text that is not in the thread (forwarded)", "--- Message 1 ---\nFrom: A <a@x.com>\nDate: d\nSubject: s\n\nPlease see below.\nOn Mon, 2 Mar 2026, Z <z@y.com> wrote:\n> The vendor said the price is 40k.\n> Delivery takes six weeks.\n> Payment on receipt.\n", False),
        Fixture("text that is not a thread", "line one\n> quoted but no message blocks\n-- \nsig\n", False),
    ],
    min_gain=0.3,
)

# ===============================================================================================================
# #422 Profiler outputs: keep rows to 95% of the total weight and add one residual row.
# cProfile (weight = tottime), jmap -histo (weight = bytes) and node .cpuprofile JSON (aggregated per function from
# samples/timeDeltas; weight = samples). perf script and Chrome trace-event JSON are not covered. At least 5 rows are always
# kept, and rows tied with the last kept one are kept, so 'the top 3 functions' never depends on where the cut falls.

_CP_HEAD = re.compile(r"^\s+ncalls\s+tottime\s+percall\s+cumtime\s+percall\s+filename:lineno\(function\)\s*$")
_CP_ROW = re.compile(r"^\s*(\d+)(?:/(\d+))?\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\S.*)$")
_CP_RES = re.compile(r"^\s+\(\+(\d+) rows, tottime (\d+\.\d+)s, ncalls (\d+)\)$")
_CP_TOT = re.compile(r"^\s+(\d+) function calls(?: \((\d+) primitive calls\))? in (\d+\.\d+) seconds\s*$")
_HI_ROW = re.compile(r"^\s*(\d+):\s+(\d+)\s+(\d+)\s+(\S.*)$")
_HI_RES = re.compile(r"^\s+\(\+(\d+) classes, (\d+) instances, (\d+) bytes\)$")
_HI_TOT = re.compile(r"^Total\s+(\d+)\s+(\d+)\s*$")
_CPU_ROW = re.compile(r"^(\d+) samples (\d+\.\d) ms  (.*) @ (.*)$")
_CPU_RES = re.compile(r"^\(\+(\d+) functions, (\d+) samples, (\d+\.\d) ms\)$")
_CPU_HEAD = re.compile(r"^cpuprofile: (\d+) samples, (\d+\.\d) ms, (\d+) functions$")
PROFILE_FRACTION = 95
PROFILE_MIN_ROWS = 5


def _cut(weights: list[int]) -> set[int]:
    """Indices of the rows to keep: the heaviest ones until 95% of the total, ties with the last kept one included, at least PROFILE_MIN_ROWS."""
    total = sum(weights)
    if total <= 0 or len(weights) <= PROFILE_MIN_ROWS + 1:
        return set(range(len(weights)))
    order = sorted(range(len(weights)), key=lambda i: (-weights[i], i))
    acc, cutoff = 0, None
    for rank, i in enumerate(order):
        acc += weights[i]
        if acc * 100 >= PROFILE_FRACTION * total and rank + 1 >= PROFILE_MIN_ROWS:
            cutoff = weights[i]
            break
    return {i for i, w in enumerate(weights) if cutoff is None or w >= cutoff}


def _enc_cprofile(lines: list[str]) -> list[str] | None:
    h = next((i for i, l in enumerate(lines) if _CP_HEAD.match(l)), None)
    if h is None:
        return None
    j = h + 1
    rows = []
    while j < len(lines) and (m := _CP_ROW.match(lines[j])):
        rows.append((lines[j], round(float(m.group(3)) * 1000), int(m.group(1))))
        j += 1
    if len(rows) < 12 or any(_CP_RES.match(l) for l in lines):
        return None
    keep = _cut([w for _, w, _ in rows])
    if len(keep) == len(rows):
        return None
    cut = [r for i, r in enumerate(rows) if i not in keep]
    res = f"   (+{len(cut)} rows, tottime {sum(w for _, w, _ in cut) / 1000:.3f}s, ncalls {sum(n for _, _, n in cut)})"
    return lines[: h + 1] + [rows[i][0] for i in sorted(keep)] + [res] + lines[j:]


def _enc_histo(lines: list[str]) -> list[str] | None:
    first = next((i for i, l in enumerate(lines) if _HI_ROW.match(l)), None)
    if first is None:
        return None
    j = first
    rows = []
    while j < len(lines) and (m := _HI_ROW.match(lines[j])):
        rows.append((lines[j], int(m.group(3)), int(m.group(2))))
        j += 1
    if len(rows) < 12 or any(_HI_RES.match(l) for l in lines):
        return None
    keep = _cut([w for _, w, _ in rows])
    if len(keep) == len(rows):
        return None
    cut = [r for i, r in enumerate(rows) if i not in keep]
    res = f"   (+{len(cut)} classes, {sum(n for _, _, n in cut)} instances, {sum(w for _, w, _ in cut)} bytes)"
    return lines[:first] + [rows[i][0] for i in sorted(keep)] + [res] + lines[j:]


def _cpu_aggregate(d) -> tuple[list[tuple], int, float] | None:
    """-> ([(samples, ms, name, url, line)], n_samples, duration ms) or None when the profile is not self-consistent."""
    try:
        nodes = {n["id"]: n["callFrame"] for n in d["nodes"]}
        samples, deltas = d["samples"], d["timeDeltas"]
        if not samples or len(samples) != len(deltas) or not all(isinstance(s, int) and s in nodes for s in samples) or not all(isinstance(t, (int, float)) for t in deltas):
            return None
        agg: dict[tuple, list] = {}
        for i, s in enumerate(samples):
            cf = nodes[s]
            key = (cf.get("functionName") or "(anonymous)", cf.get("url", ""), cf.get("lineNumber", -1))
            a = agg.setdefault(key, [0, 0.0])
            a[0] += 1
            a[1] += (deltas[i + 1] if i + 1 < len(deltas) else 0) / 1000
        rows = sorted(((v[0], v[1]) + k for k, v in agg.items()), key=lambda r: (-r[0], r[2], r[3], r[4]))
        dur = round((d["endTime"] - d["startTime"]) / 1000, 1)
        return rows, len(samples), dur
    except (KeyError, TypeError, AttributeError):
        return None


def _enc_cpuprofile(text: str) -> str | None:
    try:
        d = json.loads(text)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    agg = _cpu_aggregate(d)
    if agg is None:
        return None
    rows, n, dur = agg
    if any(("@" in r[2] and " @ " in r[2]) or "\n" in r[2] or "\n" in str(r[3]) for r in rows):
        return None
    keep = _cut([r[0] for r in rows])
    out = [f"cpuprofile: {n} samples, {dur:.1f} ms, {len(rows)} functions"]
    out += [f"{r[0]} samples {r[1]:.1f} ms  {r[2]} @ {r[3]}:{r[4]}" for i, r in enumerate(rows) if i in keep]
    cut = [r for i, r in enumerate(rows) if i not in keep]
    if cut:
        out.append(f"(+{len(cut)} functions, {sum(r[0] for r in cut)} samples, {sum(r[1] for r in cut):.1f} ms)")
    return "\n".join(out) + "\n"


def prune_profile(text: str) -> str:
    if text.lstrip().startswith("{") and '"samples"' in text:
        e = _enc_cpuprofile(text)
        return e if e is not None and len(e) < len(text) else text
    lines = text.split("\n")
    r = _enc_cprofile(lines) or _enc_histo(lines)
    if r is None:
        return text
    res = "\n".join(r)
    return res if len(res) < len(text) else text


def probes_422(text: str) -> dict:
    """Reader view: the totals, the three hottest functions, and that kept rows plus the residual row account for the whole weight."""
    lines = text.split("\n")
    total = None
    rows: list[tuple] = []  # (weight, label, extra)
    resid = (0, 0)  # rows, weight cut
    other: list[str] = []
    kind = None
    for l in lines:
        if (m := _CP_TOT.match(l)):
            total = ("calls", int(m.group(1)), int(m.group(2) or m.group(1)), m.group(3))
            kind = "cprofile"
        elif _CP_HEAD.match(l):
            kind = "cprofile"
        elif (m := _CP_ROW.match(l)) and kind == "cprofile":
            rows.append((round(float(m.group(3)) * 1000), m.group(7), int(m.group(1))))
        elif (m := _CP_RES.match(l)):
            resid = (resid[0] + int(m.group(1)), resid[1] + round(float(m.group(2)) * 1000))
            kind = kind or "cprofile"
        elif (m := _HI_ROW.match(l)):
            rows.append((int(m.group(3)), m.group(4), int(m.group(2))))
            kind = "histo"
        elif (m := _HI_RES.match(l)):
            resid = (resid[0] + int(m.group(1)), resid[1] + int(m.group(3)))
        elif (m := _HI_TOT.match(l)):
            total = ("total", int(m.group(1)), int(m.group(2)))
        elif (m := _CPU_HEAD.match(l)):
            total = ("cpu", int(m.group(1)), m.group(2), int(m.group(3)))
            kind = "cpu"
        elif (m := _CPU_ROW.match(l)):
            rows.append((int(m.group(1)), m.group(3) + " @ " + m.group(4), m.group(2)))
        elif (m := _CPU_RES.match(l)):
            resid = (resid[0] + int(m.group(1)), resid[1] + int(m.group(2)))
        else:
            other.append(l)
    if text.lstrip().startswith("{") and '"samples"' in text:
        try:
            agg = _cpu_aggregate(json.loads(text))
        except ValueError:
            agg = None
        if agg:
            r, n, dur = agg
            kind, total = "cpu", ("cpu", n, f"{dur:.1f}", len(r))
            rows = [(x[0], f"{x[2]} @ {x[3]}:{x[4]}", f"{x[1]:.1f}") for x in r]
            other = []
    top = sorted(rows, key=lambda r: (-r[0], r[1]))[:3]
    n_rows = len(rows) + resid[0]
    if kind == "cpu" and total and total[0] == "cpu" and len(rows) + resid[0] != total[3]:
        n_rows = ("inconsistent", n_rows)
    return {
        "kind": kind,
        "totals": total,
        "three heaviest rows (weight, label)": [(w, lab) for w, lab, _ in top],
        "weight accounted for (kept rows + residual)": sum(w for w, _, _ in rows) + resid[1],
        "number of rows (kept + residual)": n_rows,
        "other lines": [l for l in other if l.strip()] if kind != "cpu" else [],
    }


def _by_cumtime(text: str) -> str:
    lines = text.split("\n")
    h = next(i for i, l in enumerate(lines) if _CP_HEAD.match(l))
    rows = [l for l in lines[h + 1:] if _CP_ROW.match(l)]
    rows.sort(key=lambda l: -float(_CP_ROW.match(l).group(5)))
    head = [l.replace("internal time", "cumulative time") for l in lines[: h + 1]]
    return "\n".join(head + rows + [""])


_HISTO_SMALL = "\n".join(HISTO.split("\n")[:12]) + "\n" + "Total        390948       14227744\n"
case(
    422,
    claim="Profiler tables keep the heaviest rows to 95% of the total weight (at least 5, ties kept) and add one residual row with the cut rows' count and weight; cProfile, jmap -histo and node .cpuprofile JSON (aggregated per function) are covered. "
          "The totals line, the three heaviest rows, the number of rows and the total weight accounted for are unchanged.",
    loses="every row below the 95% cut (name, calls, cumulative time), the cumulative-time column's meaning for callers with near-zero self time, and raw .cpuprofile nodes, call tree and per-sample timestamps",
    encode=prune_profile,
    probes=probes_422,
    fixtures=[
        Fixture("cProfile -s tottime of a small search program (168 rows)", CPROFILE, True),
        Fixture("jmap -histo of a JVM with 20k small maps (311 classes)", HISTO, True),
        Fixture("node --cpu-prof .cpuprofile (61 nodes, 185 samples)", CPUPROFILE, True),
        Fixture("cProfile sorted by cumulative time, hottest function not first", _by_cumtime(CPROFILE), True),
        Fixture("cProfile with only 6 rows", "\n".join(CPROFILE.split("\n")[:12]) + "\n", False),
        Fixture("jmap -histo of a tiny process (10 classes)", _HISTO_SMALL, False),
        Fixture("json that is not a cpuprofile", json.dumps({"samples": [1, 2], "timeDeltas": [1]}), False),
        Fixture("table that already has a residual row", CPROFILE.replace("\n", "\n   (+3 rows, tottime 0.001s, ncalls 4)\n", 1), None),
    ],
    min_gain=0.3,
)

# ===============================================================================================================
# #507 lsof: fold the per-thread repeats of a process's fd table. Linux lsof (4.9x) prints the rows of every task, differing
# only in the TID and TASKCMD columns. Rows that are identical from the USER column on (and share COMMAND and PID) become one
# row whose TID cell reads 'xN' (N = rows folded, the process-level row included); one '# threads PID [...]' line per pid
# keeps the (tid, name) list. Columns are read at the offsets of the header, as lsof pads them. (The alternative, `lsof -Ki`,
# drops the thread rows at the source and loses the thread list too.)

_THREADS_LINE = re.compile(r"^# threads (\d+) (\[.*\])$")


def _lsof_cols(header: str):
    if not header.startswith("COMMAND") or "TID" not in header or "TASKCMD" not in header or "USER" not in header:
        return None
    pid_end, tid_end, cmd, user = header.index("PID") + 3, header.index("TID") + 3, header.index("TASKCMD"), header.index("USER")
    if not (pid_end < tid_end <= cmd < user):
        return None
    return pid_end, tid_end, cmd, user


def _lsof_row(line: str, cols):
    pid_end, tid_end, cmd, user = cols
    head = line[:pid_end]
    tid_cell = line[pid_end:cmd].strip()
    if len(line) <= user or len(head.split()) < 2 or not head.split()[-1].isdigit() or not (tid_cell == "" or tid_cell.isdigit()):
        return None
    return head, tid_cell, line[cmd:user].strip(), line[user:]


def fold_lsof_threads(text: str) -> str:
    lines = text.split("\n")
    cols = _lsof_cols(lines[0]) if lines else None
    if cols is None or any(_THREADS_LINE.match(l) for l in lines) or any(re.match(r"^\S+\s+\d+\s+x\d+\s", l) for l in lines[1:]):
        return text
    pid_end, tid_end, cmd_start, user = cols
    parsed = [_lsof_row(l, cols) for l in lines[1:]]
    groups: dict[tuple, list[int]] = defaultdict(list)
    threads: dict[str, list[tuple[str, str]]] = {}
    for i, r in enumerate(parsed):
        if r is None:
            continue
        groups[(r[0], r[3])].append(i)
        if r[1]:
            pid = r[0].split()[-1]
            if (r[1], r[2]) not in threads.setdefault(pid, []):
                threads[pid].append((r[1], r[2]))
    if not threads:
        return text
    out = [lines[0]]
    done: set[tuple] = set()
    for i, (l, r) in enumerate(zip(lines[1:], parsed)):
        if r is None:
            out.append(l)
            continue
        key = (r[0], r[3])
        n = len(groups[key])
        if n < 2 or n > 9999:
            out.append(l)
        elif key not in done:
            done.add(key)
            out.append(r[0] + " " + f"x{n}".ljust(cmd_start - pid_end - 1) + " " * (user - cmd_start) + r[3])
    trailer = [f"# threads {pid} " + json.dumps([list(t) for t in th], separators=(",", ":")) for pid, th in threads.items()]
    if trailer and out and out[-1] == "":
        out = out[:-1] + trailer + [""]
    else:
        out += trailer
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def probes_507(text: str) -> dict:
    """Reader view by header offsets: each distinct (command, pid, fd row) with how many task rows it stood for, and the (tid, name) list of every pid."""
    lines = text.split("\n")
    cols = _lsof_cols(lines[0]) if lines else None
    if cols is None:
        return {"unparsed": text}
    rows: Counter = Counter()
    threads: dict[str, list] = {}
    other: list[str] = []
    for l in lines[1:]:
        if (m := _THREADS_LINE.match(l)):
            threads.setdefault(m.group(1), [])
            for t in json.loads(m.group(2)):
                if tuple(t) not in map(tuple, threads[m.group(1)]):
                    threads[m.group(1)].append(t)
            continue
        r = _lsof_row(l, cols) if l else None
        if r is None:
            # a folded row has 'xN' in the TID cell
            head, cell = l[:cols[0]], l[cols[0]:cols[2]].strip()
            if l and re.fullmatch(r"x\d+", cell) and len(l) > cols[3] and head.split()[-1:] and head.split()[-1].isdigit():
                rows[(" ".join(head.split()), l[cols[3]:])] += int(cell[1:])
                continue
            other.append(l)
            continue
        head, tid, cmd, rest = r
        rows[(" ".join(head.split()), rest)] += 1
        if tid:
            ts = threads.setdefault(head.split()[-1], [])
            if [tid, cmd] not in ts:
                ts.append([tid, cmd])
    return {
        "distinct rows (command pid, fd columns) with task multiplicity": sorted(rows.items()),
        "threads of each pid (tid, name)": sorted((pid, sorted(map(tuple, ts))) for pid, ts in threads.items()),
        "other lines": other,
    }


def make_lsof(n_threads: int = 10, extra_pid: bool = True) -> str:
    lines = LSOF_MAIN.split("\n")
    header, main = lines[0], [l for l in lines[1:] if l.strip()]
    out = [header] + main
    for tid, cmd in LSOF_THREADS[:n_threads]:
        out += [m[:12] + " " + tid.rjust(4) + " " + cmd.ljust(9) + " " + m[28:] for m in main]
    if extra_pid:  # a second, single-threaded process (blank TID cells)
        base = "python3  8211                "
        out += [base + "root  cwd       DIR  254,0      4096 712833 /srv/app", base + "root  rtd       DIR  254,0      4096      2 /",
                base + "root  txt       REG  254,0   7899464 156703 /usr/bin/python3.13",
                base + "root    3u     IPv4  91123       0t0    TCP *:5432 (LISTEN)", base + "root    4u     IPv4  91130       0t0    TCP 10.0.0.5:5432->10.0.0.9:51322 (ESTABLISHED)"]
    return "\n".join(out) + "\n"


_PLAIN_LSOF = "\n".join(["COMMAND  PID USER   FD      TYPE DEVICE  SIZE/OFF   NODE NAME"] + [l[:12] + l[28 - 4:] for l in LSOF_MAIN.split("\n")[1:] if l.strip()][:20]) + "\n"
case(
    507,
    claim="lsof -K style output (COMMAND PID TID TASKCMD ...): rows identical from the USER column on across a process's threads fold into one row whose TID cell reads 'xN', and one '# threads PID [[tid,name],...]' line per pid keeps the thread list; "
          "every distinct file, port and pid with the number of task rows it stood for, and every (tid, name), are unchanged.",
    loses="the order in which the per-thread rows were printed, and which thread's row was the representative (all are identical by construction)",
    encode=fold_lsof_threads,
    probes=probes_507,
    fixtures=[
        Fixture("node with 10 threads + a single-threaded python3 (real rows, 62 fds per task)", make_lsof(10), True),
        Fixture("3 threads, no second process", make_lsof(3, False), True),
        Fixture("process whose threads hold different fds (rows differ: nothing to fold for the odd one)", make_lsof(3, False).replace("node    5791 5796 node      root   44u     IPv4  16388       0t0    TCP localhost:8089 (LISTEN)", "node    5791 5796 node      root   44u     IPv4  16399       0t0    TCP localhost:9090 (LISTEN)"), True),
        Fixture("plain lsof -p (no TID/TASKCMD columns)", _PLAIN_LSOF, False),
        Fixture("threads header but only process-level rows", "\n".join(LSOF_MAIN.split("\n")[:20]) + "\n", False),
        Fixture("not lsof", "Compiling core v0.4.2\n", False),
        Fixture("output that already has a threads trailer", make_lsof(3, False).replace("\n", "\n# threads 5791 [[\"5794\",\"x\"]]\n", 1), None),
    ],
    min_gain=0.5,
)

# ===============================================================================================================
# #518 git ls-remote of a GitHub remote: collapse the refs/pull/N/head and /merge series to number ranges, keep HEAD, every
# branch and the 10 newest tags with their shas, and reduce older tags to a names-only line. Needs >= 20 pull refs (resp. > 10
# tags) to act, so `git ls-remote origin refs/pull/12/head` (a ref named in the command) is left alone.
# 'Newest' = highest tuple of the integers in the tag name; tags without digits (latest, stable) always keep their line.
# Real format: '<40 hex>\t<refname>' sorted by refname (so pull/10 sorts before pull/2); peeled annotated tags as 'refname^{}'.

_LS = re.compile(r"^([0-9a-f]{40})\t(\S+)$")
_PULL = re.compile(r"^refs/pull/(\d+)/(head|merge)$")
_PULL_SUM = re.compile(r"^pull (head|merge) refs \((\d+)\): ([\d,\-]+)$")
_OLD_TAGS = re.compile(r"^older tags, names only \((\d+)\): (\S.*)$")
NEWEST_TAGS = 10


def _tag_key(name: str):
    nums = tuple(int(x) for x in re.findall(r"\d+", name))
    return (nums, name) if nums else None


def _ranges(nums: list[int]) -> str:
    out, s = [], sorted(set(nums))
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[j] + 1:
            j += 1
        out.append(str(s[i]) if i == j else f"{s[i]}-{s[j]}")
        i = j + 1
    return ",".join(out)


def _expand(expr: str) -> list[int]:
    out = []
    for part in expr.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def fold_refs(text: str) -> str:
    trail = text.endswith("\n")
    lines = (text[:-1] if trail else text).split("\n")
    if any(_PULL_SUM.match(l) or _OLD_TAGS.match(l) for l in lines):
        return text
    m_all = [_LS.match(l) for l in lines]
    pulls = {"head": [], "merge": []}
    for m in m_all:
        if m and (p := _PULL.match(m.group(2))):
            pulls[p.group(2)].append(int(p.group(1)))
    n_pull = len(pulls["head"]) + len(pulls["merge"])
    tags = [m.group(2)[10:] for m in m_all if m and m.group(2).startswith("refs/tags/") and not m.group(2).endswith("^{}")]
    versioned = sorted({t for t in tags if _tag_key(t)}, key=_tag_key, reverse=True)
    fold_tags = len(versioned) > NEWEST_TAGS + 8
    old = set(versioned[NEWEST_TAGS:]) if fold_tags else set()
    if n_pull < 20 and not old:
        return text
    out: list[str] = []
    pull_done = tags_done = False
    for l, m in zip(lines, m_all):
        if m and n_pull >= 20 and _PULL.match(m.group(2)):
            if not pull_done:
                pull_done = True
                for kind in ("head", "merge"):
                    if pulls[kind]:
                        out.append(f"pull {kind} refs ({len(set(pulls[kind]))}): {_ranges(pulls[kind])}")
            continue
        if m and m.group(2).startswith("refs/tags/"):
            name = m.group(2)[10:]
            base = name[:-3] if name.endswith("^{}") else name
            if base in old:
                if not tags_done:
                    tags_done = True
                    out.append(f"older tags, names only ({len(old)}): " + " ".join(sorted(old, key=_tag_key)))
                continue
        out.append(l)
    # duplicates in the pull series would make the count ambiguous: refuse
    if len(set(pulls["head"])) != len(pulls["head"]) or len(set(pulls["merge"])) != len(pulls["merge"]):
        return text
    res = "\n".join(out) + ("\n" if trail else "")
    return res if len(res) < len(text) else text


def probes_518(text: str) -> dict:
    """Reader view: HEAD, every branch with its sha, all tag names, the shas of the 10 newest tags, the PR numbers with head / merge refs, other refs verbatim."""
    heads, other, tag_sha, tag_names, pull = [], [], {}, set(), {"head": set(), "merge": set()}
    stray = []
    for l in text.split("\n"):
        if (m := _LS.match(l)):
            sha, ref = m.groups()
            if ref.startswith("refs/heads/") or ref == "HEAD":
                heads.append((sha, ref))
            elif ref.startswith("refs/tags/"):
                name = ref[10:]
                if name.endswith("^{}"):
                    tag_sha[name] = sha
                else:
                    tag_names.add(name)
                    tag_sha[name] = sha
            elif (p := _PULL.match(ref)):
                pull[p.group(2)].add(int(p.group(1)))
            else:
                other.append(l)
        elif (m := _PULL_SUM.match(l)):
            pull[m.group(1)].update(_expand(m.group(3)))
        elif (m := _OLD_TAGS.match(l)):
            tag_names.update(m.group(2).split(" "))
        else:
            stray.append(l)
    ranked = sorted({t for t in tag_names if _tag_key(t)}, key=_tag_key, reverse=True)[:NEWEST_TAGS]
    nums = lambda s: sorted(s)  # noqa: E731
    return {
        "HEAD and branches (sha, ref)": heads,
        "all tag names": sorted(tag_names),
        "newest tags with sha (and peeled sha)": [(t, tag_sha.get(t), tag_sha.get(t + "^{}")) for t in ranked],
        "tags without digits (full lines)": sorted((t, tag_sha.get(t)) for t in tag_names if not _tag_key(t) and t in tag_sha),
        "PR numbers with a head ref": nums(pull["head"]),
        "PR numbers with a merge ref": nums(pull["merge"]),
        "other refs": other,
        "other lines": [l for l in stray if l],
    }


def _sha(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()


def make_ls_remote(n_prs: int = 1500, n_tags: int = 90, seed: int = 4) -> str:
    rnd = random.Random(seed)
    refs = [("HEAD", _sha("main"))]
    for b in ["main", "develop", "release/1.x", "release/2.x", "gh-pages", "dependabot/npm_and_yarn/lodash-4.17.21", "dependabot/pip/urllib3-2.2.1", "feature/oauth-device-flow", "fix/flaky-retry-test"]:
        refs.append((f"refs/heads/{b}", _sha(b)))
    nums = sorted(rnd.sample(range(1, int(n_prs * 2.3)), n_prs))
    for n in nums:
        refs.append((f"refs/pull/{n}/head", _sha(f"h{n}")))
        if n > nums[-1] - 120 or rnd.random() < 0.04:
            refs.append((f"refs/pull/{n}/merge", _sha(f"m{n}")))
    for i in range(n_tags):
        t = f"v{i // 30}.{i // 5 % 6}.{i % 5}"
        refs.append((f"refs/tags/{t}", _sha("t" + t)))
        if i % 2 == 0:
            refs.append((f"refs/tags/{t}^{{}}", _sha("c" + t)))
    refs += [("refs/tags/latest", _sha("latest")), ("refs/tags/nightly", _sha("nightly"))]
    refs.sort(key=lambda r: (r[0] != "HEAD", r[0]))
    return "".join(f"{s}\t{r}\n" for r, s in refs)


_LSR = make_ls_remote()
case(
    518,
    claim="git ls-remote output: with 20+ pull refs the refs/pull/N/head and /merge series become 'pull head refs (N): ranges' lines, and with more than 18 versioned tags the older ones become one names-only line; HEAD, every branch, "
          "the 10 newest tags with their shas (and peeled shas), tags without digits, every tag name, and the exact set of PR numbers with a head and with a merge ref are unchanged.",
    loses="the shas of all pull refs and of tags older than the 10 newest, and the peeled shas of those tags",
    encode=fold_refs,
    probes=probes_518,
    fixtures=[
        Fixture("1,500 PRs (sparse numbers, ~120 open with merge refs), 9 branches, 92 tags", _LSR, True),
        Fixture("small remote: 60 PRs, 12 tags", make_ls_remote(60, 12, seed=2), True),
        Fixture("many tags, no PRs (a plain git host)", "".join(l + "\n" for l in make_ls_remote(0, 70, seed=3).split("\n") if l), True),
        Fixture("ls-remote naming one ref (refs/pull/12/head)", f"{_sha('h12')}\trefs/pull/12/head\n", False),
        Fixture("19 pull refs and 3 tags (under the thresholds)", "".join(f"{_sha(str(i))}\trefs/pull/{i}/head\n" for i in range(1, 20)) + "".join(f"{_sha('t' + str(i))}\trefs/tags/v1.0.{i}\n" for i in range(3)), False),
        Fixture("not ls-remote", "Compiling core v0.4.2\n", False),
        Fixture("output that already holds a summary line", _LSR.replace("\n", "\npull head refs (3): 1-3\n", 1), None),
    ],
    min_gain=0.5,
)

# ===============================================================================================================
# #613 Stub QR codes, figlet banners and terminal block art. A run of 4+ consecutive lines made only of block-element or
# braille glyphs (QR codes from expo / wrangler / aws sso; ANSI colour codes are ignored), or of figlet-style ASCII glyphs,
# is replaced by '[block art omitted: R lines x C cols (kind)]'. ASCII art is matched narrowly because it is the family that
# could eat real text: only the characters _ | / \ ( ) ' ` , . < > (no letters, digits, + - = * :), at least 3 different
# glyphs and 3 different lines in the run, and one of _ ( ) ' ` , . < > among them (git log --graph and tables have none of
# those without text beside them); a '#'-only family needs 5 lines and 3 different lines. Side-by-side art with text (neofetch)
# is NOT handled: those lines carry words, so they never match.

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_ART_BLOCK = re.compile(r"^[ \xa0\u2580-\u259f\u2800-\u28ff]*$")  # python-qrcode prints NO-BREAK SPACE between blocks
_ART_ASCII = re.compile(r"^[ \xa0_|/\\()'`,.<>]*$")
_ART_HASH = re.compile(r"^[ \xa0#]*$")
_STUB_ART = re.compile(r"^\[block art omitted: (\d+) lines x (\d+) cols \((block|braille|ascii)\)\]$")


def _art_family(line: str) -> str | None:
    t = _ANSI.sub("", line).rstrip()
    body = t.replace(" ", "").replace("\xa0", "")
    if not body:
        return None
    if _ART_BLOCK.match(t):
        return "block" if len(body) >= 4 else None
    if len(body) >= 4 and _ART_ASCII.match(t):
        return "ascii"
    if len(body) >= 4 and _ART_HASH.match(t):
        return "hash"
    return None


def _run_ok(fam: str, run: list[str]) -> bool:
    texts = [_ANSI.sub("", l).rstrip() for l in run]
    if fam == "block":
        return len(run) >= 4
    if fam == "hash":
        return len(run) >= 5 and len(set(texts)) >= 3
    glyphs = {c for t in texts for c in t if c not in " \xa0"}
    return len(run) >= 4 and len(glyphs) >= 3 and len(set(texts)) >= 3 and bool(glyphs & set("_()'`,.<>"))


def _runs(lines: list[str]) -> list[tuple[int, int, str]]:
    """[(start, end exclusive, kind)] of art runs."""
    out, i = [], 0
    fams = [_art_family(l) for l in lines]
    while i < len(lines):
        f = fams[i]
        if f is None:
            i += 1
            continue
        j = i
        while j < len(lines) and fams[j] == f:
            j += 1
        if _run_ok(f, lines[i:j]):
            kind = "ascii" if f in ("ascii", "hash") else ("braille" if not any("\u2580" <= c <= "\u259f" for l in lines[i:j] for c in l) else "block")
            out.append((i, j, kind))
        i = j
    return out


def stub_art(text: str) -> str:
    lines = text.split("\n")
    if any(_STUB_ART.match(l) for l in lines):
        return text
    runs = _runs(lines)
    if not runs:
        return text
    out, pos = [], 0
    for s, e, kind in runs:
        out += lines[pos:s]
        cols = max(len(_ANSI.sub("", l).rstrip()) for l in lines[s:e])
        out.append(f"[block art omitted: {e - s} lines x {cols} cols ({kind})]")
        pos = e
    out += lines[pos:]
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def probes_613(text: str) -> dict:
    """Sequence view: every text line verbatim in place, and each art block as (kind, rows, cols); runs found by counting glyph classes per line."""
    lines = text.split("\n")
    seq: list = []
    run: list[str] = []
    run_fam = None

    def flush():
        nonlocal run, run_fam
        if run:
            ok = _run_ok(run_fam, run)
            if ok:
                kinds = {"block" if "\u2580" <= c <= "\u259f" else "braille" for l in run for c in l if "\u2580" <= c <= "\u259f" or "\u2800" <= c <= "\u28ff"}
                kind = "ascii" if run_fam in ("ascii", "hash") else ("block" if "block" in kinds else "braille")
                seq.append(("art", kind, len(run), max(len(_ANSI.sub("", l).rstrip()) for l in run)))
            else:
                seq.extend(("line", l) for l in run)
        run, run_fam = [], None

    for l in lines:
        if (m := _STUB_ART.match(l)):
            flush()
            seq.append(("art", m.group(3), int(m.group(1)), int(m.group(2))))
            continue
        # classify by counting characters instead of regexes
        t = _ANSI.sub("", l).rstrip()
        body = [c for c in t if c not in " \xa0"]
        if not body:
            fam = None
        elif all("\u2580" <= c <= "\u259f" or "\u2800" <= c <= "\u28ff" for c in body):
            fam = "block" if len(body) >= 4 and all(c in " \xa0" or "\u2580" <= c <= "\u259f" or "\u2800" <= c <= "\u28ff" for c in t) else None
        elif len(body) >= 4 and all(c in "_|/\\()'`,.<>" for c in body):
            fam = "ascii"
        elif len(body) >= 4 and all(c == "#" for c in body):
            fam = "hash"
        else:
            fam = None
        if fam is not None and (run_fam is None or fam == run_fam):
            run.append(l)
            run_fam = fam
        else:
            flush()
            if fam is not None:
                run, run_fam = [l], fam
            else:
                seq.append(("line", l))
    flush()
    return {"sequence of text lines and art blocks (kind, rows, cols)": seq}


_EXPO = "Starting Metro Bundler\n" + ART_QR + "\n› Metro waiting on exp://192.168.1.23:8081\n› Scan the QR code above with Expo Go (Android) or the Camera app (iOS)\n\n› Press s │ switch to development build\n› Press a │ open Android\n"
_ANSI_QR = "\n".join(("\x1b[47m\x1b[30m" + l + "\x1b[0m") if l else l for l in ART_QR.split("\n"))
_DEPLOY = ("$ ./deploy.sh production\nbuilding image acme/web:2.3.1 ... done\n" + ART_FIG_STD + "pushing to registry ... done\nrolling out 3 replicas\n" + ART_FIG_SLANT +
           "health checks passed (3/3)\n" + ART_FIG_BIG + "all done\n")
_NEG_GRAPH = "* bf1922c docs/ideas: gate cases checkpoint\n* 56e9126 docs/ideas: rename lossless/ to gate/\n*   7070068 Merge main (squash of #4)\n|\\  \n| * 0b85430 docs/ideas: runnable fidelity gate\n* | 26bf41b docs/ideas: runnable fidelity gate\n|/  \n* 3e0adc3 checkpoint\n"
_NEG_GRAPH_PURE = "| |\\  \\\n| | | |\n| |/ /\n| | |\n|/| |\n| | |\n| |\\ \\\n"
_NEG_TABLE = "+--------+-------+\n| name   | count |\n+--------+-------+\n| alpha  |     3 |\n| beta   |    12 |\n+--------+-------+\n"
_NEG_BOX = "┌────────┬───────┐\n│ name   │ count │\n├────────┼───────┤\n│ alpha  │     3 │\n│ beta   │    12 │\n└────────┴───────┘\n"
_NEG_TREE = ".\n├── docs\n│   ├── ideas\n│   │   ├── dropped.md\n│   │   └── shortlist.md\n│   └── README.md\n├── src\n│   └── main.py\n└── README.md\n"
_NEG_MISC = ("Progress ████████░░░░░░░░ 52%\nspark ▁▂▃▅▇█▇▅▃▂\n################\n# build banner #\n################\n----------------\n"
             "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n>>> print(1)\n1\n>>>\n>>>\n")
_NEG_PROSE = "The quick brown fox jumps over the lazy dog. (See the appendix, section 3.)\nIt's a well-known pangram, used to test typefaces.\nOne, two, three... and so on.\nThis is only prose, even with / and \\ and _ in it.\n"
_NEG_SHORT_QR = "\n".join(ART_QR.split("\n")[:3]) + "\n"
case(
    613,
    claim="A run of 4+ consecutive lines of only block-element/braille glyphs (QR codes, ANSI colour ignored) or figlet-style ASCII glyphs becomes one '[block art omitted: R lines x C cols (kind)]' line; every other line, in place, is byte-identical, "
          "and prose, markdown and box-drawing tables, tree output, git log --graph, progress bars, diffs and code banners never match.",
    loses="the art itself (a QR code's payload, the glyph shapes) and the text a figlet banner spelled, and any art drawn side by side with words (neofetch logos), which is not stubbed at all",
    encode=stub_art,
    probes=probes_613,
    fixtures=[
        Fixture("expo start: QR code (14 lines x 27 cols) between text lines", _EXPO, True),
        Fixture("the same QR with ANSI colour codes on every row", _EXPO.replace(ART_QR, _ANSI_QR), True),
        Fixture("deploy log with figlet standard, slant and big banners (art at start, middle, end)", _DEPLOY, True),
        Fixture("art first and art last", ART_FIG_STD + "ok\n" + ART_QR, True),
        Fixture("braille image (6 lines x 60 cols)", "preview:\n" + ART_BRAILLE + "done\n", True),
        Fixture("banner3 figlet made of '#'", "building\n" + ART_FIG_BANNER + "finished\n", True),
        Fixture("prose with / \\ _ ( ) in it", _NEG_PROSE, False),
        Fixture("markdown table and ASCII table", "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\n" + _NEG_TABLE, False),
        Fixture("box-drawing table", _NEG_BOX, False),
        Fixture("tree output", _NEG_TREE, False),
        Fixture("git log --graph", _NEG_GRAPH, False),
        Fixture("pure graph columns of a busy merge history (no text beside them)", _NEG_GRAPH_PURE, False),
        Fixture("progress bar, sparkline, banner comment, hr, diff, REPL prompts", _NEG_MISC, False),
        Fixture("QR-like block art of only 3 lines", _NEG_SHORT_QR, False),
        Fixture("text that already looks like a stub", "[block art omitted: 3 lines x 5 cols (ascii)]\n" + ART_QR, None),
    ],
    min_gain=0.3,
)

# ===============================================================================================================
# #728 Digest HAR files to one line per request (HAR 1.2 as exported by Chrome DevTools; reconstructed from memory).
# 'METHOD url status ms Nbytes type' per entry; failed (status >= 400 or 0), slow (>= 1000 ms), the 5 slowest (ties included),
# XHR/fetch, documents, redirects and non-GET rows stay, with the first 160 characters of a text body on failed, slow and XHR
# rows; successful static GETs (script, stylesheet, image, font, media) fold into one 'static (N requests: ...)' line.

_HAR_ROW = re.compile(r"^([A-Z]+) (\S+) (\d{3}) (\d+)ms (\d+)B (\S+)$")
_HAR_BODY = re.compile(r'^  body: (".*")$')
_HAR_STATIC = re.compile(r"^static \((\d+) requests: ((?:[a-z]+=\d+ ?)+)\): (\d+)B, slowest (\d+)ms$")
_HAR_HEAD = re.compile(r"^HAR (\d+) entries, (\d+)B transferred$")
_STATIC_TYPES = {"script", "stylesheet", "image", "font", "media"}
_MIME_TYPE = (("text/html", "document"), ("javascript", "script"), ("text/css", "stylesheet"), ("image/", "image"), ("font", "font"), ("json", "xhr"))


def _har_type(e: dict) -> str:
    t = e.get("_resourceType")
    if isinstance(t, str) and t.isalpha():
        return t.lower()
    mime = ((e.get("response") or {}).get("content") or {}).get("mimeType", "")
    return next((v for k, v in _MIME_TYPE if k in mime), "other")


def _har_entries(d):
    try:
        es = d["log"]["entries"]
        out = []
        for e in es:
            req, resp = e["request"], e["response"]
            status = resp["status"]
            if not isinstance(req["method"], str) or not req["method"].isalpha() or not req["method"].isupper() or not isinstance(req["url"], str) or not req["url"] or any(c.isspace() for c in req["url"]):
                return None
            if isinstance(status, bool) or not isinstance(status, int) or not 0 <= status <= 999 or isinstance(e["time"], bool) or not isinstance(e["time"], (int, float)) or not 0 <= e["time"] < 1e9:
                return None
            size = resp.get("_transferSize", (resp.get("content") or {}).get("size", 0))
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                size = max(0, int(resp.get("bodySize", 0) or 0)) if isinstance(resp.get("bodySize", 0), int) else 0
            out.append((req["method"], req["url"], status, round(e["time"]), size, _har_type(e), e))
        return out if out else None
    except (KeyError, TypeError, AttributeError):
        return None


def _har_body(e: dict) -> str | None:
    c = (e.get("response") or {}).get("content") or {}
    t = c.get("text")
    if isinstance(t, str) and c.get("encoding") != "base64" and t:
        return t[:160]
    return None


def digest_har(text: str) -> str:
    if not text.lstrip().startswith("{") or '"entries"' not in text:
        return text
    try:
        d = json.loads(text)
    except ValueError:
        return text
    ents = _har_entries(d)
    if ents is None:
        return text
    times = sorted((r[3] for r in ents), reverse=True)
    top_cut = times[min(4, len(times) - 1)]
    rows, static = [], []
    for r in ents:
        m, u, st, ms, size, ty, e = r
        keep = st >= 400 or st == 0 or ms >= 1000 or ms >= top_cut or ty not in _STATIC_TYPES or m != "GET" or st not in (200, 304)
        (rows if keep else static).append(r)
    out = [f"HAR {len(ents)} entries, {sum(r[4] for r in ents)}B transferred"]
    placed = False
    for r in ents:
        m, u, st, ms, size, ty, e = r
        if r in static:
            if not placed:
                placed = True
                hist = Counter(x[5] for x in static)
                out.append(f"static ({len(static)} requests: " + " ".join(f"{k}={v}" for k, v in sorted(hist.items())) + f"): {sum(x[4] for x in static)}B, slowest {max(x[3] for x in static)}ms")
            continue
        out.append(f"{m} {u} {st} {ms}ms {size}B {ty}")
        if (st >= 400 or st == 0 or ms >= 1000 or ty in ("xhr", "fetch")) and (b := _har_body(e)):
            out.append("  body: " + json.dumps(b, ensure_ascii=False))
    res = "\n".join(out) + "\n"
    return res if len(res) < len(text) else text


def probes_728(text: str) -> dict:
    """Reader view from the JSON (walk the entries) or from the digest (parse rows): totals, failures, slowest five, XHR rows, type histogram."""
    rows: list[tuple] = []  # (method, url, status, ms, bytes, type)
    hist: Counter = Counter()
    n_static = bytes_static = 0
    total_bytes = None
    try:
        d = json.loads(text) if text.lstrip().startswith("{") else None
    except ValueError:
        d = None
    ents = _har_entries(d) if d is not None else None
    bodies: dict[int, str] = {}
    if ents is not None:
        for i, r in enumerate(ents):
            rows.append(r[:6])
            hist[r[5]] += 1
        total_bytes = sum(r[4] for r in ents)
        n_entries = len(ents)
    else:
        n_entries = None
        lines = text.split("\n")
        for l in lines:
            if (m := _HAR_ROW.match(l)):
                rows.append((m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5)), m.group(6)))
                hist[m.group(6)] += 1
            elif (m := _HAR_STATIC.match(l)):
                n_static += int(m.group(1))
                bytes_static += int(m.group(3))
                for kv in m.group(2).split():
                    k, v = kv.split("=")
                    hist[k] += int(v)
            elif (m := _HAR_HEAD.match(l)):
                n_entries, total_bytes = int(m.group(1)), int(m.group(2))
        if n_entries is None:
            return {"unparsed": text}
    slowest = sorted(((r[3], r[1], r[0]) for r in rows), key=lambda x: (-x[0], x[1], x[2]))[:5]
    return {
        "entries and total transferred bytes": (n_entries, total_bytes),
        "failed requests (status >= 400 or 0)": [(r[0], r[1], r[2], r[3]) for r in rows if r[2] >= 400 or r[2] == 0],
        "five slowest (ms, url)": [(a, b) for a, b, _ in slowest],
        "xhr and fetch requests": [(r[0], r[1], r[2]) for r in rows if r[5] in ("xhr", "fetch")],
        "requests per resource type": sorted(hist.items()),
        "rows at or above 1000 ms": [(r[0], r[1], r[3]) for r in rows if r[3] >= 1000],
    }


def make_har(n_static: int = 36, seed: int = 9) -> str:
    rnd = random.Random(seed)
    base = "https://app.example.com"
    hdr_req = [{"name": ":authority", "value": "app.example.com"}, {"name": ":method", "value": "GET"}, {"name": ":scheme", "value": "https"}, {"name": "accept", "value": "*/*"},
               {"name": "accept-encoding", "value": "gzip, deflate, br"}, {"name": "accept-language", "value": "en-US,en;q=0.9,nl;q=0.8"}, {"name": "cookie", "value": "sid=" + "ab12" * 12 + "; theme=dark"},
               {"name": "referer", "value": base + "/"}, {"name": "sec-ch-ua", "value": '"Chromium";v="119", "Not?A_Brand";v="24"'}, {"name": "sec-fetch-mode", "value": "cors"},
               {"name": "user-agent", "value": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"}]
    hdr_resp = [{"name": "cache-control", "value": "public, max-age=31536000, immutable"}, {"name": "content-encoding", "value": "br"}, {"name": "content-type", "value": "application/javascript"},
                {"name": "date", "value": "Mon, 02 Mar 2026 10:15:30 GMT"}, {"name": "etag", "value": 'W/"5f2c-18a3b4c5d6e"'}, {"name": "server", "value": "nginx"}, {"name": "vary", "value": "Accept-Encoding"},
                {"name": "x-request-id", "value": "%032x" % rnd.getrandbits(128)}]
    ents = []

    def entry(method, path, status, ms, rtype, mime, size, text=None, enc=None):
        resp_content = {"size": size, "mimeType": mime}
        if text is not None:
            resp_content["text"] = text
        if enc:
            resp_content["encoding"] = enc
        t = {"blocked": round(rnd.random() * 3, 3), "dns": -1, "ssl": -1, "connect": -1, "send": 0.3, "wait": round(ms * 0.8, 3), "receive": round(ms * 0.2, 3), "_blocked_queueing": 0.8}
        return {"_initiator": {"type": "parser", "url": base + "/", "lineNumber": 14}, "_priority": "High", "_resourceType": rtype, "cache": {}, "connection": "443", "pageref": "page_1",
                "request": {"method": method, "url": base + path, "httpVersion": "h2", "headers": hdr_req, "queryString": [], "cookies": [{"name": "sid", "value": "ab12" * 12}], "headersSize": -1, "bodySize": 0},
                "response": {"status": status, "statusText": "", "httpVersion": "h2", "headers": hdr_resp, "cookies": [], "content": resp_content, "redirectURL": "", "headersSize": -1, "bodySize": size // 3, "_transferSize": size // 3 + 310},
                "serverIPAddress": "203.0.113.7", "startedDateTime": "2026-03-02T10:15:30.123Z", "time": round(ms, 3), "timings": t}
    ents.append(entry("GET", "/", 200, 312.4, "document", "text/html", 14211, "<!doctype html><html><head><title>Acme</title></head><body><div id=root></div>" * 3))
    for i in range(n_static):
        kind = ("script", "stylesheet", "image", "font")[i % 4]
        mime = {"script": "application/javascript", "stylesheet": "text/css", "image": "image/png", "font": "font/woff2"}[kind]
        ext = {"script": "js", "stylesheet": "css", "image": "png", "font": "woff2"}[kind]
        ents.append(entry("GET", f"/assets/{kind}-{i}.{ext}", 304 if i % 9 == 0 else 200, 20 + rnd.random() * 180, kind, mime, 2000 + rnd.randrange(60000), "QUJD" if kind == "image" else None, "base64" if kind == "image" else None))
    ents.insert(12, entry("GET", "/api/products?page=1&limit=24", 200, 288.9, "fetch", "application/json", 5321, '{"items":[{"id":1,"name":"Anvil","price":19.99}],"total":240,"page":1}'))
    ents.insert(20, entry("POST", "/api/login", 401, 188.0, "xhr", "application/json", 41, '{"error":"invalid credentials","attempts_left":2}'))
    ents.insert(30, entry("GET", "/assets/script-slow.js", 200, 1850.2, "script", "application/javascript", 450000))
    ents.insert(33, entry("GET", "/api/search?q=anvil", 200, 2301.7, "xhr", "application/json", 91820, '{"hits":' + "[" + '{"id":%d,"title":"result"},' * 5 + "]}"))
    ents.insert(40, entry("GET", "/favicon.ico", 404, 41.2, "other", "text/html", 0))
    ents.append(entry("GET", "/api/cart", 500, 977.1, "fetch", "application/json", 112, '{"error":"upstream timeout","trace":"c1f9e2"}'))
    ents.append(entry("POST", "/api/events", 204, 55.0, "ping", "text/plain", 0))
    ents.append(entry("GET", "/assets/hero-large.png", 200, 1004.0, "image", "image/png", 880000, "QUJD", "base64"))
    har = {"log": {"version": "1.2", "creator": {"name": "WebInspector", "version": "537.36"}, "pages": [{"startedDateTime": "2026-03-02T10:15:30.000Z", "id": "page_1", "title": base + "/", "pageTimings": {"onContentLoad": 812.4, "onLoad": 1411.9}}], "entries": ents}}
    return json.dumps(har, indent=2) + "\n"


_HAR = make_har()
case(
    728,
    claim="A HAR file becomes one 'METHOD url status ms bytes type' row per kept entry (failed, slow, 5 slowest, XHR/fetch, documents, non-GET; 160 characters of text body on failed, slow and XHR rows) plus one 'static (N requests: ...)' line; "
          "the entry count, total bytes, every 4xx/5xx/status-0 request, the five slowest, every XHR/fetch request, the request count per resource type and every row at or above 1000 ms are unchanged.",
    loses="request and response headers, cookies, timings breakdown, initiator, IPs, base64 bodies, bodies beyond 160 characters, and the identity of individual successful static assets (only their per-type count, bytes and slowest time stay)",
    encode=digest_har,
    probes=probes_728,
    fixtures=[
        Fixture("Chrome HAR: 1 document, 36 static assets, 6 XHR/fetch (401, 500), slow script, 404 favicon, 880 kB image", _HAR, True),
        Fixture("small HAR: 8 static assets", make_har(8, seed=3), True),
        Fixture("HAR whose entries have no response (cut-off export): left alone", json.dumps({"log": {"entries": [{"request": {"method": "GET", "url": "https://x.io/"}, "time": 1}] * 5}}, indent=2), False),
        Fixture("JSON that is not a HAR", json.dumps({"entries": [1, 2]}), False),
        Fixture("entry with a space in the url (left alone)", json.dumps({"log": {"entries": [{"request": {"method": "GET", "url": "https://x.io/a b"}, "response": {"status": 200, "content": {"size": 3, "mimeType": "text/css"}}, "time": 1.0, "_resourceType": "stylesheet"}] * 9}}, indent=2), False),
        Fixture("text that already looks like a digest", "HAR 3 entries, 10B transferred\nGET https://x.io/ 200 5ms 10B document\n", None),
    ],
    min_gain=0.5,
)

# ===============================================================================================================
# #729 OCR engine outputs (tesseract hOCR and TSV; reconstructed from memory of tesseract 5) to plain text lines.
# One text line per ocr_line, a blank line between paragraphs/blocks, '[page N]' before pages 2+, words under confidence 60
# written 'word[?NN]'. hOCR is read with html.parser, the probe re-reads it with regular expressions, and the encoder only
# rewrites when both agree (a cheap self-check: truncated or mangled markup is left alone). Textract / Vision JSON are not
# covered. Word geometry, baselines, font sizes and the conf of confident words are dropped.

CONF_FLOOR = 60
_FLAG_W = re.compile(r"^(.*)\[\?(\d+)\]$")
_PAGE_MARK = re.compile(r"^\[page (\d+)\]$")


class _HocrParser(__import__("html.parser", fromlist=["HTMLParser"]).HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pages: list[list[list[list[tuple[str, float | None]]]]] = []  # page -> paragraph -> line -> (word, conf)
        self.word = None
        self.depth_word = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        if tag == "div" and cls == "ocr_page":
            self.pages.append([])
        elif tag == "p" and cls == "ocr_par" and self.pages:
            self.pages[-1].append([])
        elif tag == "span" and cls == "ocr_line" and self.pages and self.pages[-1]:
            self.pages[-1][-1].append([])
        elif tag == "span" and cls == "ocrx_word" and self.pages and self.pages[-1] and self.pages[-1][-1]:
            m = re.search(r"x_wconf (-?\d+(?:\.\d+)?)", a.get("title", ""))
            self.word = ["", float(m.group(1)) if m else None]
            self.depth_word = 1
        elif self.word is not None and tag == "span":
            self.depth_word += 1

    def handle_endtag(self, tag):
        if self.word is not None and tag == "span":
            self.depth_word -= 1
            if self.depth_word == 0:
                w = "".join(self.word[0].split())
                if w:
                    self.pages[-1][-1][-1].append((w, self.word[1]))
                self.word = None

    def handle_data(self, data):
        if self.word is not None:
            self.word[0] += data


def _render_pages(pages) -> str | None:
    out: list[str] = []
    for pi, page in enumerate(pages):
        if pi > 0:
            out.append(f"[page {pi + 1}]")
        first_par = True
        for par in page:
            lines = []
            for line in par:
                ws = [f"{w}[?{round(c)}]" if c is not None and c < CONF_FLOOR else w for w, c in line]
                if any("[?" in w and not _FLAG_W.match(w) for w in ws) or any(("[?" in w) for w, _ in line):
                    return None
                if ws:
                    lines.append(" ".join(ws))
            if lines:
                if not first_par:
                    out.append("")
                out += lines
                first_par = False
    return "\n".join(out) + "\n" if out else None


def _tsv_pages(text: str):
    rows = [l.split("\t") for l in text.rstrip("\n").split("\n")]
    if not rows or rows[0][:12] != ["level", "page_num", "block_num", "par_num", "line_num", "word_num", "left", "top", "width", "height", "conf", "text"]:
        return None
    pages: list = []
    last = (None, None, None, None)
    for r in rows[1:]:
        if len(r) != 12 or not all(x.lstrip("-").isdigit() for x in r[:10]):
            return None
        try:
            conf = float(r[10])
        except ValueError:
            return None
        if r[0] != "5" or not r[11].strip():
            continue
        page, block, par, line = r[1], r[2], r[3], r[4]
        if not pages or page != last[0]:
            pages.append([])
            last = (page, None, None, None)
        if (block, par) != last[1:3]:
            pages[-1].append([])
            last = (page, block, par, None)
        if line != last[3]:
            pages[-1][-1].append([])
            last = (page, block, par, line)
        pages[-1][-1][-1].append((r[11].strip(), conf if conf >= 0 else None))
    return pages


def ocr_to_text(text: str) -> str:
    pages = None
    if "ocrx_word" in text and "<html" in text.lower():
        p = _HocrParser()
        try:
            p.feed(text)
            p.close()
        except Exception:  # noqa: BLE001 (an HTML parser on mangled input)
            return text
        pages = p.pages
    elif text.startswith("level\tpage_num\t"):
        pages = _tsv_pages(text)
    if not pages:
        return text
    out = _render_pages(pages)
    if out is None or len(out) >= len(text) or probes_729(out) != probes_729(text):
        return text
    return out


def _model_from_text(text: str):
    pages = [[]]
    for line in text.split("\n"):
        m = _PAGE_MARK.match(line)
        if m:
            pages.append([])
        elif line == "":
            pages[-1].append([]) if pages[-1] and pages[-1][-1] else None
        elif line:
            if not pages[-1]:
                pages[-1].append([])
            pages[-1][-1].append(line.split(" "))
    return [[par for par in page if par] for page in pages]


def probes_729(text: str) -> dict:
    """Reader view: pages -> paragraphs -> lines of words, and every low-confidence word with its confidence. hOCR is read with regexes, TSV by columns, plain text by splitting."""
    lines_out: list = []
    flagged: list = []
    npages = None
    if "ocrx_word" in text and "<html" in text.lower():
        events = []
        for m in re.finditer(r"<div class=['\"]ocr_page['\"]|<p class=['\"]ocr_par['\"]|<span class=['\"]ocr_line['\"]|<span class=['\"]ocrx_word['\"][^>]*?title=['\"]([^'\"]*)['\"][^>]*>(.*?)</span>", text, re.S):
            events.append(m)
        page = par = line = -1
        cur: dict = {}
        for m in events:
            g = m.group(0)
            if g.startswith("<div"):
                page, par, line = page + 1, -1, -1
            elif g.startswith("<p"):
                par, line = par + 1, -1
            elif g.startswith("<span class='ocr_line") or g.startswith('<span class="ocr_line'):
                line += 1
            else:
                if page < 0 or par < 0 or line < 0:
                    continue
                w = "".join(html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).split())
                conf = re.search(r"x_wconf (-?\d+(?:\.\d+)?)", m.group(1))
                c = float(conf.group(1)) if conf else None
                if w:
                    cur.setdefault((page, par, line), []).append((w, c))
        npages = page + 1
        grouped: dict = {}
        for (pg, pr, ln), ws in sorted(cur.items()):
            grouped.setdefault((pg, pr), []).append(ws)
        for (pg, pr), lns in sorted(grouped.items()):
            lines_out.append((pg, pr, tuple(tuple(w for w, _ in l) for l in lns)))
            for li, l in enumerate(lns):
                for w, c in l:
                    if c is not None and c < CONF_FLOOR:
                        flagged.append((pg, pr, li, w, round(c)))
        # renumber pages/paragraphs densely (empty ones vanish in the rendering)
        dense: list = []
        pm, qm = {}, {}
        for pg, pr, lns in lines_out:
            pm.setdefault(pg, len(pm))
            qm.setdefault((pg, pr), sum(1 for k in qm if k[0] == pg))
            dense.append((pm[pg], qm[(pg, pr)], lns))
        lines_out = dense
        flagged = [(pm[pg], qm[(pg, pr)], li, w, c) for pg, pr, li, w, c in flagged]
        npages = len(pm)
    elif text.startswith("level\tpage_num\t") and (pg := _tsv_pages(text)) is not None:
        for pi, page in enumerate(pg):
            for qi, par in enumerate([p for p in page if any(p)]):
                lines_out.append((pi, qi, tuple(tuple(w for w, _ in l) for l in par if l)))
                for li, l in enumerate([l for l in par if l]):
                    for w, c in l:
                        if c is not None and c < CONF_FLOOR:
                            flagged.append((pi, qi, li, w, round(c)))
        npages = len(pg)
    else:
        model = _model_from_text(text)
        npages = len([p for p in model if p]) if any(model) else 0
        pi_map = [i for i, p in enumerate(model) if p]
        for pi, page in enumerate(p for p in model if p):
            for qi, par in enumerate(page):
                clean = []
                for li, ws in enumerate(par):
                    cw = []
                    for w in ws:
                        m = _FLAG_W.match(w)
                        if m:
                            flagged.append((pi, qi, li, m.group(1), int(m.group(2))))
                            cw.append(m.group(1))
                        else:
                            cw.append(w)
                    clean.append(tuple(cw))
                lines_out.append((pi, qi, tuple(clean)))
    return {"pages": npages, "paragraphs of lines of words (page, paragraph, lines)": lines_out, "words under the confidence floor (page, paragraph, line, word, conf)": flagged}


_INVOICE = [
    ["Invoice", "No.", "2026-00417"], ["Acme", "Industrial", "Supply", "B.V."], ["Keizersgracht", "12,", "1015", "CJ", "Amsterdam"],
    None,
    ["Bill", "to:", "Northwind", "Traders"], ["Attn:", "Accounts", "Payable"], ["PO", "number", "88-4410"],
    None,
    ["Item", "Qty", "Unit", "price", "Total"], ["Hex", "bolt", "M8x40", "500", "0.12", "60.00"], ["Washer", "M8", "1000", "0.03", "30.00"], ["Anvil", "20kg", "2", "89.50", "179.00"],
    ["Shipping", "and", "handling", "25.00"],
    None,
    ["Subtotal", "294.00"], ["VAT", "21%", "61.74"], ["Total", "due", "EUR", "355.74"],
    None,
    ["Payment", "within", "30", "days", "to", "IBAN", "NL91", "ABNA", "0417", "4354", "43"], ["Thank", "you", "for", "your", "business!"],
]
_LOWCONF = {"2026-00417": 41, "88-4410": 52, "NL91": 38, "4354": 55, "Anvil": 58, "Attn:": 47}


def make_ocr(fmt: str = "hocr", pages: int = 1) -> str:
    def conf(w: str, k: int) -> int:
        return _LOWCONF.get(w, 90 + (len(w) * 7 + k * 3) % 10)
    if fmt == "tsv":
        out = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
        for pg in range(1, pages + 1):
            out.append(f"1\t{pg}\t0\t0\t0\t0\t0\t0\t1654\t2339\t-1\t")
            block, y, line_no = 0, 140, 0
            par_open = False
            for ln in _INVOICE:
                if ln is None:
                    par_open = False
                    y += 40
                    continue
                if not par_open:
                    block += 1
                    line_no = 0
                    out += [f"2\t{pg}\t{block}\t0\t0\t0\t148\t{y}\t1358\t61\t-1\t", f"3\t{pg}\t{block}\t1\t0\t0\t148\t{y}\t1358\t61\t-1\t"]
                    par_open = True
                line_no += 1
                out.append(f"4\t{pg}\t{block}\t1\t{line_no}\t0\t148\t{y}\t1358\t61\t-1\t")
                x = 148
                for k, w in enumerate(ln, 1):
                    out.append(f"5\t{pg}\t{block}\t1\t{line_no}\t{k}\t{x}\t{y}\t{len(w) * 22}\t61\t{conf(w, k) + (0.123 if k % 2 else 0.5):.6f}\t{w}")
                    x += len(w) * 22 + 19
                y += 70
        return "\n".join(out) + "\n"
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"', '    "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">',
           '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">', ' <head>', '  <title></title>', '  <meta http-equiv="Content-Type" content="text/html;charset=utf-8" />',
           "  <meta name='ocr-system' content='tesseract 5.3.0' />", "  <meta name='ocr-capabilities' content='ocr_page ocr_carea ocr_par ocr_line ocrx_word ocrp_wconf' />", ' </head>', ' <body>']
    for pg in range(1, pages + 1):
        out.append(f"  <div class='ocr_page' id='page_{pg}' title='image \"scan{pg}.png\"; bbox 0 0 1654 2339; ppageno {pg - 1}; scan_res 300 300'>")
        block, y, par_open, wid, lid = 0, 140, False, 0, 0
        for ln in _INVOICE:
            if ln is None:
                if par_open:
                    out += ["    </p>", "   </div>"]
                par_open = False
                y += 40
                continue
            if not par_open:
                block += 1
                out += [f"   <div class='ocr_carea' id='block_{pg}_{block}' title=\"bbox 148 {y} 1506 {y + 61}\">", f"    <p class='ocr_par' id='par_{pg}_{block}' lang='eng' title=\"bbox 148 {y} 1506 {y + 61}\">"]
                par_open = True
            lid += 1
            out.append(f"     <span class='ocr_line' id='line_{pg}_{lid}' title=\"bbox 148 {y} 1506 {y + 61}; baseline 0 -12; x_size 53; x_descenders 11; x_ascenders 13\">")
            x = 148
            for k, w in enumerate(ln, 1):
                wid += 1
                esc = w.replace("&", "&amp;")
                out.append(f"      <span class='ocrx_word' id='word_{pg}_{wid}' title='bbox {x} {y} {x + len(w) * 22} {y + 61}; x_wconf {conf(w, k)}'>" + ("<strong>" + esc + "</strong>" if k == 1 and ln[0] in ("Invoice", "Total") else esc) + "</span>")
                x += len(w) * 22 + 19
            out.append("     </span>")
            y += 70
        if par_open:
            out += ["    </p>", "   </div>"]
        out.append("  </div>")
    out += [" </body>", "</html>"]
    return "\n".join(out) + "\n"


_HOCR = make_ocr("hocr")
case(
    729,
    claim="tesseract hOCR or TSV becomes plain text lines (a blank line between paragraphs, '[page N]' before later pages) with words under confidence 60 written 'word[?NN]'; the words of every line in order, "
          "the paragraph and page structure and every low-confidence word with its confidence are unchanged (the rewrite is skipped when the hOCR parse and an independent regex parse disagree).",
    loses="word, line and block bounding boxes, baselines, font sizes, ids, the confidence of confident words, bold markup, and the image name and resolution",
    encode=ocr_to_text,
    probes=probes_729,
    fixtures=[
        Fixture("hOCR of an invoice page (6 low-confidence words, bold <strong> words, '&amp;'-free)", _HOCR, True),
        Fixture("tesseract TSV of the same page", make_ocr("tsv"), True),
        Fixture("two-page hOCR", make_ocr("hocr", pages=2), True),
        Fixture("two-page TSV", make_ocr("tsv", pages=2), True),
        Fixture("hOCR with an entity inside a word", _HOCR.replace(">Industrial<", ">R&amp;D<", 1), True),
        Fixture("plain tesseract text output", "Invoice No. 2026-00417\nAcme Industrial Supply B.V.\n\nBill to: Northwind Traders\n", False),
        Fixture("html that is not hOCR", "<html><body><p class='x'>hello</p></body></html>\n", False),
        Fixture("word that already looks like a flag", _HOCR.replace(">Industrial<", ">odd[?12]<", 1), None),
    ],
    min_gain=0.5,
)

# ===============================================================================================================
# #760 Collapse printed LlamaIndex NodeWithScore reprs (print(retriever.retrieve(q)), response.source_nodes). Each node becomes
# one valid-Python line: NodeWithScore(id=..., score=..., start=..., end=..., metadata={...}, text=...). Kept: the id, the score,
# the character offsets, the metadata entries that are NOT in excluded_llm_metadata_keys (what the LLM sees: the file path,
# page label, ...), and the text exactly. Dropped: embedding=None, both excluded_*_metadata_keys lists, relationships (with the
# metadata and hash of the source document repeated per node), metadata_template/separator, text_template, mimetype. The field
# order of TextNode is reconstructed from memory of llama-index-core 0.10-0.12. LangChain Document reprs have no constant
# fields to drop and stay as they are. The encoder scans the repr with a string-aware bracket matcher, the probe re-reads it
# with regexes and ast; the rewrite happens only when both agree.

_NWS_START = "NodeWithScore(node=TextNode("
_STR = r"""'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*\""""
_NODE_LINE = re.compile(r"^NodeWithScore\(id=")


def _skip_str(s: str, i: int) -> int:
    q = s[i]
    i += 1
    while i < len(s):
        if s[i] == "\\":
            i += 2
        elif s[i] == q:
            return i + 1
        else:
            i += 1
    raise ValueError("unterminated string")


def _match(s: str, i: int, open_c: str = "(", close_c: str = ")") -> int:
    """Index just after the bracket matching s[i], skipping string literals."""
    depth = 0
    while i < len(s):
        c = s[i]
        if c in "'\"":
            i = _skip_str(s, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced")


def _split_top(s: str) -> list[str]:
    parts, depth, i, start = [], 0, 0, 0
    while i < len(s):
        c = s[i]
        if c in "'\"":
            i = _skip_str(s, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append(s[start:i].strip())
            start = i + 1
        i += 1
    parts.append(s[start:].strip())
    return parts


def _node_line(node_args: str, score: str) -> str:
    f = {}
    for p in _split_top(node_args):
        k, _, v = p.partition("=")
        f[k.strip()] = v.strip()
    meta = ast.literal_eval(f["metadata"])
    excl = ast.literal_eval(f.get("excluded_llm_metadata_keys", "[]"))
    kept = {k: v for k, v in meta.items() if k not in excl}
    ast.literal_eval(f["text"])  # must be one string literal
    return (f"NodeWithScore(id={f['id_']}, score={score}, start={f.get('start_char_idx', 'None')}, end={f.get('end_char_idx', 'None')}, "
            f"metadata={kept!r}, text={f['text']})")


def collapse_nodes(text: str) -> str:
    i = text.find("[" + _NWS_START)
    if i < 0 or text.count("[" + _NWS_START) > 1:
        return text
    lines: list[str] = []
    try:
        j = i + 1
        while text.startswith(_NWS_START, j):
            end = _match(text, j + len("NodeWithScore"))  # the NodeWithScore( ... ) call
            inner = text[j + len("NodeWithScore("): end - 1]
            parts = _split_top(inner)
            if len(parts) != 2 or not parts[0].startswith("node=TextNode(") or not parts[1].startswith("score="):
                return text
            node_args = parts[0][len("node=TextNode("):-1]
            lines.append(_node_line(node_args, parts[1][6:]))
            j = end
            if text.startswith(", ", j):
                j += 2
        if not text.startswith("]", j) or not lines:
            return text
        j += 1
    except (ValueError, SyntaxError, KeyError):
        return text
    res = text[:i] + "\n".join(lines) + text[j:]
    if len(res) >= len(text) or probes_760(res) != probes_760(text):
        return text
    return res


def probes_760(text: str) -> dict:
    """Per node: id, score, offsets, the LLM-visible metadata and the text; read with regexes (original repr) or ast (collapsed lines)."""
    nodes: list[tuple] = []
    outside: list[str] = []
    for ln in text.split("\n"):
        if _NODE_LINE.match(ln):
            try:
                call = ast.parse(ln, mode="eval").body
                kw = {k.arg: ast.literal_eval(k.value) for k in call.keywords}
                nodes.append((kw["id"], kw["score"], kw["start"], kw["end"], tuple(sorted(kw["metadata"].items())), kw["text"]))
                continue
            except (SyntaxError, ValueError, KeyError):
                pass
        pos = 0
        found = False
        got: list[tuple] = []
        try:
            while (k := ln.find("[" + _NWS_START if pos == 0 else _NWS_START, pos)) >= 0:
                found = True
                m_id = re.compile(r"id_=(" + _STR + ")").search(ln, k)
                m_meta = re.compile(r"metadata=(\{[^{}]*\}), excluded_embed_metadata_keys=").search(ln, k)
                m_ex = re.compile(r"excluded_llm_metadata_keys=(\[[^\]]*\])").search(ln, k)
                m_txt = re.compile(r", text=(" + _STR + r"), mimetype=").search(ln, k)
                m_s = re.compile(r", start_char_idx=(\d+|None), end_char_idx=(\d+|None)").search(ln, k)
                m_sc = re.compile(r"\), score=([-0-9.eE+]+|None)\)").search(ln, m_txt.end() if m_txt else k)
                if not (m_id and m_meta and m_ex and m_txt and m_s and m_sc):
                    found = False
                    break
                meta = ast.literal_eval(m_meta.group(1))
                excl = ast.literal_eval(m_ex.group(1))
                got.append((ast.literal_eval(m_id.group(1)), ast.literal_eval(m_sc.group(1)), ast.literal_eval(m_s.group(1)), ast.literal_eval(m_s.group(2)),
                            tuple(sorted((a, b) for a, b in meta.items() if a not in excl)), ast.literal_eval(m_txt.group(1))))
                pos = m_sc.end()
        except (SyntaxError, ValueError, AttributeError, TypeError):
            found = False
        if found:
            nodes.extend(got)
        if not found:
            outside.append(ln)
    return {"nodes (id, score, start, end, llm-visible metadata, text)": nodes, "other lines": outside}


def make_nodes(n: int = 8, text_len: int = 700, seed: int = 21) -> str:
    rnd = random.Random(seed)
    files = [("/home/dev/docs/guide.md", "guide.md", 4821), ("/home/dev/docs/api/auth.md", "auth.md", 9210), ("/home/dev/docs/api/webhooks.md", "webhooks.md", 7342), ("/home/dev/docs/faq.md", "faq.md", 2210),
             ("/home/dev/docs/changelog.md", "changelog.md", 15012)]
    sentences = ["Tokens expire after fifteen minutes and must be refreshed with the refresh endpoint.", "Webhook payloads are signed with HMAC-SHA256; verify the `X-Signature` header before parsing.",
                 "Rate limits are per API key: 600 requests per minute on the Team plan, 3,000 on Business.", "If you see a 429 response, back off exponentially and honour the Retry-After header.",
                 "Use idempotency keys for POST requests so that retries never create duplicate orders.", "Don't store secrets in the repository; use the secret manager's \"read-only\" role instead.",
                 "The sandbox mirrors production but resets every night at 03:00 UTC.", "Pagination uses opaque cursors; never construct them by hand."]
    ex = "['file_name', 'file_type', 'file_size', 'creation_date', 'last_modified_date', 'last_accessed_date']"
    nodes = []
    for k in range(n):
        path, name, size = files[k % len(files)]
        meta = f"{{'file_path': '{path}', 'file_name': '{name}', 'file_type': 'text/markdown', 'file_size': {size}, 'creation_date': '2026-03-02', 'last_modified_date': '2026-03-02'" + (", 'page_label': '%d'" % (k + 1) if k % 3 == 0 else "") + "}"
        txt = ""
        while len(txt) < text_len:
            txt += rnd.choice(sentences) + (" " if rnd.random() < 0.7 else "\n\n")
        doc = "%032x" % rnd.getrandbits(128)
        rel = (f"{{<NodeRelationship.SOURCE: '1'>: RelatedNodeInfo(node_id='{doc[:8]}-{doc[8:12]}-{doc[12:16]}-{doc[16:20]}-{doc[20:]}', node_type=<ObjectType.DOCUMENT: '4'>, metadata={meta}, hash='{'%064x' % rnd.getrandbits(256)}'), "
               f"<NodeRelationship.PREVIOUS: '2'>: RelatedNodeInfo(node_id='{'%032x' % rnd.getrandbits(128)}', node_type=<ObjectType.TEXT: '1'>, metadata={{}}, hash='{'%064x' % rnd.getrandbits(256)}'), "
               f"<NodeRelationship.NEXT: '3'>: RelatedNodeInfo(node_id='{'%032x' % rnd.getrandbits(128)}', node_type=<ObjectType.TEXT: '1'>, metadata={{}}, hash='{'%064x' % rnd.getrandbits(256)}')}}")
        nid = "%032x" % rnd.getrandbits(128)
        start = 1024 * (k % 4)
        nodes.append(f"NodeWithScore(node=TextNode(id_='{nid[:8]}-{nid[8:12]}-{nid[12:16]}-{nid[16:20]}-{nid[20:]}', embedding=None, metadata={meta}, excluded_embed_metadata_keys={ex}, excluded_llm_metadata_keys={ex}, "
                     f"relationships={rel}, metadata_template='{{key}}: {{value}}', metadata_separator='\\n', text={txt!r}, mimetype='text/plain', start_char_idx={start}, end_char_idx={start + len(txt)}, "
                     f"metadata_seperator='\\n', text_template='{{metadata_str}}\\n\\n{{content}}'), score={0.8921 - 0.0317 * k:.4f})")
    return "[" + ", ".join(nodes) + "]\n"


_NODES = make_nodes()
case(
    760,
    claim="A printed list of LlamaIndex NodeWithScore reprs becomes one valid-Python 'NodeWithScore(id=..., score=..., start=..., end=..., metadata={...}, text=...)' line per node; the id, score, character offsets, "
          "the metadata entries the LLM is shown (not in excluded_llm_metadata_keys) and the text exactly are unchanged; embedding, excluded-key lists, relationships, templates and mimetype are dropped.",
    loses="the embedding field, both excluded_*_metadata_keys lists, the relationships (previous/next/source node ids, the source document's hash and its metadata copy), the templates and separators, mimetype, and metadata entries listed in excluded_llm_metadata_keys",
    encode=collapse_nodes,
    probes=probes_760,
    fixtures=[
        Fixture("top-8 retrieval, 700-character chunks from 5 markdown files", _NODES, True),
        Fixture("top-3 retrieval with 2,500-character chunks (default-size chunks)", make_nodes(3, 2500, seed=4), True),
        Fixture("top-8 retrieval with 120-character chunks (the finder's small synthetic case)", make_nodes(8, 120, seed=6), True),
        Fixture("nodes printed after other output lines", "Retrieved 8 nodes:\n" + _NODES + "done\n", True),
        Fixture("LangChain Document repr (nothing constant to drop)", "[Document(metadata={'source': 'guide.md', 'page': 3}, page_content='Tokens expire after fifteen minutes.'), Document(metadata={'source': 'auth.md', 'page': 1}, page_content='Rate limits are per key.')]\n", False),
        Fixture("print(node) str form", "Node ID: 4f1c8a2e\nText: Tokens expire after fifteen minutes.\nScore:  0.892\n", False),
        Fixture("a text that contains the repr marker inside the chunk", _NODES.replace("Tokens expire", "[NodeWithScore(node=TextNode( Tokens expire", 1), None),
    ],
    min_gain=0.25,
)

# ===============================================================================================================
# #765 Strip layout geometry and confidences from document-parser JSON dumps: Unstructured (list of elements), Docling
# (texts / tables with prov), Azure Document Intelligence (analyzeResult). Per schema, geometry (coordinates, bbox, polygon),
# probabilities (detection_class_prob, confidence), duplicates of text (text_as_html, orig == text, words, lines and the
# top-level content when paragraphs exist), span offsets, ids and parent links and per-file constants are removed; the
# remaining JSON keeps its structure, so the same reader works on both forms. Shapes reconstructed from memory of the three
# parsers' JSON output; text, type/label/role and page of every element are kept.

_UNS_META_KEEP = {"page_number", "category_depth", "link_urls"}
_DOCLING_DROP = {"bbox", "charspan", "self_ref", "parent", "children", "content_layer", "image"}
_AZURE_DROP = {"polygon", "confidence", "spans", "words", "styles", "selectionMarks"}


def _drop_keys(o, keys):
    if isinstance(o, dict):
        return {k: _drop_keys(v, keys) for k, v in o.items() if k not in keys}
    if isinstance(o, list):
        return [_drop_keys(v, keys) for v in o]
    return o


def _schema(d):
    if isinstance(d, list) and d and all(isinstance(e, dict) and isinstance(e.get("type"), str) and isinstance(e.get("text"), str) for e in d):
        return "unstructured"
    if isinstance(d, dict) and isinstance(d.get("texts"), list) and isinstance(d.get("body"), dict):
        return "docling"
    if isinstance(d, dict) and isinstance(d.get("analyzeResult"), dict) and isinstance(d["analyzeResult"].get("pages"), list):
        return "azure"
    return None


def strip_parser_json(text: str) -> str:
    if not text.lstrip().startswith(("[", "{")):
        return text
    try:
        d = json.loads(text)
    except ValueError:
        return text
    kind = _schema(d)
    if kind is None:
        return text
    if kind == "unstructured":
        out = []
        for e in d:
            md = e.get("metadata")
            ne = {k: v for k, v in e.items() if k not in ("element_id", "metadata")}
            if isinstance(md, dict):
                ne["metadata"] = {k: v for k, v in md.items() if k in _UNS_META_KEEP}
            out.append(ne)
    elif kind == "docling":
        out = {k: v for k, v in d.items() if k not in ("pages", "origin", "furniture", "groups", "pictures_data")}
        out = _drop_keys(out, _DOCLING_DROP)
        for t in out.get("texts", []):
            if isinstance(t, dict) and t.get("orig") == t.get("text"):
                t.pop("orig", None)
        for t in out.get("tables", []):
            if isinstance(t, dict):
                t.pop("orig", None)
    else:
        ar = dict(d["analyzeResult"])
        has_par = isinstance(ar.get("paragraphs"), list)
        drops = set(_AZURE_DROP) | ({"lines"} if has_par else set())
        ar = _drop_keys(ar, drops)
        if has_par:
            ar.pop("content", None)
        out = {k: v for k, v in d.items() if k != "analyzeResult"}
        out["analyzeResult"] = ar
    res = json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ("\n" if text.endswith("\n") else "")
    if len(res) >= len(text) or probes_765(res) != probes_765(text):
        return text
    return res


def _page_of(o) -> int | None:
    """First page number found under an element dict, whatever the parser calls it."""
    if isinstance(o, dict):
        for k in ("page_number", "page_no", "pageNumber"):
            if isinstance(o.get(k), int):
                return o[k]
        for v in o.values():
            if (p := _page_of(v)) is not None:
                return p
    elif isinstance(o, list):
        for v in o:
            if (p := _page_of(v)) is not None:
                return p
    return None


def probes_765(text: str) -> dict:
    """Walk the JSON: (type/label/role, page, text) of every element, table cells with their row and column, the page count."""
    try:
        d = json.loads(text)
    except ValueError:
        return {"unparsed": text}
    kind = _schema(d)
    if kind == "unstructured":
        return {"schema": kind, "elements (type, page, text)": [(e["type"], _page_of(e.get("metadata")), e["text"]) for e in d]}
    if kind == "docling":
        cells = []
        for ti, t in enumerate(d.get("tables", [])):
            for c in ((t.get("data") or {}).get("table_cells") or []) if isinstance(t, dict) else []:
                cells.append((ti, c.get("start_row_offset_idx"), c.get("start_col_offset_idx"), c.get("text")))
        return {"schema": kind, "elements (label, page, text)": [(t.get("label"), _page_of(t.get("prov")), t.get("text")) for t in d["texts"] if isinstance(t, dict)],
                "table cells (table, row, col, text)": cells, "table pages": [_page_of(t.get("prov")) for t in d.get("tables", []) if isinstance(t, dict)]}
    if kind == "azure":
        ar = d["analyzeResult"]
        cells = [(ti, c.get("rowIndex"), c.get("columnIndex"), c.get("content")) for ti, t in enumerate(ar.get("tables", [])) for c in t.get("cells", [])]
        pars = [(p.get("role"), _page_of(p.get("boundingRegions")), p.get("content")) for p in ar.get("paragraphs", [])] if isinstance(ar.get("paragraphs"), list) else None
        lines = None if pars is not None else [(pg.get("pageNumber"), [ln.get("content") for ln in pg.get("lines", [])]) for pg in ar["pages"]]
        return {"schema": kind, "paragraphs (role, page, content)": pars, "lines per page (when no paragraphs)": lines, "table cells (table, row, col, content)": cells,
                "page numbers": [pg.get("pageNumber") for pg in ar["pages"]]}
    return {"schema": None, "unparsed": text}


_PARAS = [("Title", "Quarterly Operations Review"), ("NarrativeText", "Revenue grew 12 percent year over year, driven mainly by the Team plan price change introduced in March."),
          ("Header", "Acme Inc | Confidential"), ("NarrativeText", "Churn stayed flat at 2.1 percent. Support tickets fell by a fifth after the new onboarding flow shipped."),
          ("ListItem", "Migrate the billing service by March 14."), ("ListItem", "Freeze the schema on March 1."), ("Table", "Region Q1 Q2 EMEA 120 134 AMER 98 101"),
          ("NarrativeText", "The cutover is owned by Bob; the customer email by Alice. Legal review is booked for Monday."), ("Footer", "Page 2 of 7")]


def make_unstructured(n: int = 3) -> str:
    rnd = random.Random(5)
    els = []
    for pg in range(1, n + 1):
        for k, (ty, tx) in enumerate(_PARAS):
            x, y = 100 + rnd.randrange(40), 150 + k * 190 + rnd.randrange(20)
            md = {"detection_class_prob": round(0.80 + rnd.random() * 0.19, 4),
                  "coordinates": {"points": [[x, y], [x, y + 90], [x + 1480, y + 90], [x + 1480, y]], "system": "PixelSpace", "layout_width": 1700, "layout_height": 2200},
                  "last_modified": "2026-03-02T10:15:30", "filetype": "application/pdf", "languages": ["eng"], "page_number": pg, "file_directory": "/data/reports", "filename": "q-review.pdf"}
            if ty == "Title":
                md["category_depth"] = 0
            if ty == "Table":
                md["text_as_html"] = "<table><thead><tr><th>Region</th><th>Q1</th><th>Q2</th></tr></thead><tbody><tr><td>EMEA</td><td>120</td><td>134</td></tr><tr><td>AMER</td><td>98</td><td>101</td></tr></tbody></table>"
            if k > 1:
                md["parent_id"] = "%032x" % rnd.getrandbits(128)
            els.append({"type": ty, "element_id": "%032x" % rnd.getrandbits(128), "text": tx, "metadata": md})
    return json.dumps(els, indent=2) + "\n"


def make_docling(n: int = 3) -> str:
    rnd = random.Random(8)
    texts, tables = [], []
    for pg in range(1, n + 1):
        for k, (ty, tx) in enumerate(_PARAS):
            if ty == "Table":
                cells = [{"bbox": {"l": 70.2, "t": 400.1 + r * 18, "r": 520.9, "b": 415.0 + r * 18, "coord_origin": "BOTTOMLEFT"}, "row_span": 1, "col_span": 1, "start_row_offset_idx": r, "end_row_offset_idx": r + 1, "start_col_offset_idx": c,
                          "end_col_offset_idx": c + 1, "text": f"{r}{c}", "column_header": r == 0, "row_header": False, "row_section": False} for r in range(3) for c in range(3)]
                tables.append({"self_ref": f"#/tables/{len(tables)}", "parent": {"$ref": "#/body"}, "children": [], "label": "table", "prov": [{"page_no": pg, "bbox": {"l": 70.2, "t": 400.1, "r": 520.9, "b": 460.0, "coord_origin": "BOTTOMLEFT"}, "charspan": [0, 0]}],
                               "data": {"table_cells": cells, "num_rows": 3, "num_cols": 3}})
                continue
            label = {"Title": "title", "NarrativeText": "text", "Header": "page_header", "Footer": "page_footer", "ListItem": "list_item"}[ty]
            texts.append({"self_ref": f"#/texts/{len(texts)}", "parent": {"$ref": "#/body"}, "children": [], "content_layer": "body", "label": label,
                          "prov": [{"page_no": pg, "bbox": {"l": 72.0 + rnd.random(), "t": 700.5 - k * 60, "r": 540.0, "b": 680.2 - k * 60, "coord_origin": "BOTTOMLEFT"}, "charspan": [0, len(tx)]}], "orig": tx, "text": tx})
    d = {"schema_name": "DoclingDocument", "version": "1.0.0", "name": "q-review", "origin": {"mimetype": "application/pdf", "binary_hash": 13987445581, "filename": "q-review.pdf"},
         "furniture": {"self_ref": "#/furniture", "children": [], "name": "_root_", "label": "unspecified"}, "body": {"self_ref": "#/body", "children": [{"$ref": f"#/texts/{i}"} for i in range(len(texts))], "name": "_root_", "label": "unspecified"},
         "groups": [], "texts": texts, "pictures": [], "tables": tables, "pages": {str(p): {"size": {"width": 612.0, "height": 792.0}, "page_no": p} for p in range(1, n + 1)}}
    return json.dumps(d, indent=2) + "\n"


def make_azure(n: int = 2) -> str:
    rnd = random.Random(2)
    pages, paras, off = [], [], 0
    content = ""
    for pg in range(1, n + 1):
        words, lines = [], []
        for k, (ty, tx) in enumerate(_PARAS):
            if ty == "Table":
                continue
            poly = [round(0.9 + rnd.random() * 0.1, 4), round(1 + k * 0.9, 4), 7.6, round(1 + k * 0.9, 4), 7.6, round(1.3 + k * 0.9, 4), 0.9, round(1.3 + k * 0.9, 4)]
            role = {"Title": "title", "Header": "pageHeader", "Footer": "pageFooter"}.get(ty)
            p = {"spans": [{"offset": off, "length": len(tx)}], "boundingRegions": [{"pageNumber": pg, "polygon": poly}], "content": tx}
            if role:
                p["role"] = role
            paras.append(p)
            lines.append({"content": tx, "polygon": poly, "spans": [{"offset": off, "length": len(tx)}]})
            for w in tx.split():
                words.append({"content": w, "polygon": poly, "confidence": round(0.93 + rnd.random() * 0.07, 3), "span": {"offset": off, "length": len(w)}})
            content += tx + "\n"
            off += len(tx) + 1
        pages.append({"pageNumber": pg, "angle": 0, "width": 8.5, "height": 11, "unit": "inch", "words": words, "lines": lines, "spans": [{"offset": 0, "length": off}]})
    tables = [{"rowCount": 3, "columnCount": 3, "cells": [{"rowIndex": r, "columnIndex": c, "content": f"{r}{c}", "boundingRegions": [{"pageNumber": 1, "polygon": [1, 2, 3, 4, 5, 6, 7, 8]}], "spans": [{"offset": 5, "length": 2}]} for r in range(3) for c in range(3)]}]
    d = {"status": "succeeded", "createdDateTime": "2026-03-02T10:15:30Z", "analyzeResult": {"apiVersion": "2023-07-31", "modelId": "prebuilt-layout", "stringIndexType": "textElements", "content": content, "pages": pages,
                                                                                        "paragraphs": paras, "tables": tables, "styles": [{"isHandwritten": False, "confidence": 0.95, "spans": [{"offset": 0, "length": 50}]}]}}
    return json.dumps(d, indent=2) + "\n"


case(
    765,
    claim="Unstructured, Docling and Azure Document Intelligence JSON dumps lose their geometry (coordinates, bbox, polygon), probabilities, text duplicates (text_as_html, orig, words, lines, top-level content), span offsets, ids and parent links "
          "and are re-emitted as compact JSON; the type/label/role, page and text of every element, every table cell with its row and column, and the page numbers are unchanged.",
    loses="coordinates and polygons, confidences and detection probabilities, element ids and parent links (the document hierarchy), table HTML, word-level records, span offsets, page sizes and per-file constants (filename, filetype, languages)",
    encode=strip_parser_json,
    probes=probes_765,
    fixtures=[
        Fixture("Unstructured partition_pdf elements (27, with coordinates and probabilities)", make_unstructured(), True),
        Fixture("Docling export (texts with prov bbox, table cells with bbox)", make_docling(), True),
        Fixture("Azure Document Intelligence prebuilt-layout (words, lines, paragraphs, tables)", make_azure(), True),
        Fixture("Azure result without paragraphs (older api: lines carry the text)", json.dumps({"analyzeResult": {"pages": [{"pageNumber": 1, "words": [{"content": "hi", "polygon": [1, 2, 3, 4, 5, 6, 7, 8], "confidence": 0.99}] * 30,
                                                                                                                      "lines": [{"content": "hi hi", "polygon": [1, 2, 3, 4, 5, 6, 7, 8]}]}]}}, indent=2), True),
        Fixture("JSON that is neither", json.dumps({"texts": "x", "body": 1, "other": [1, 2, 3]}, indent=2), False),
        Fixture("elements that carry no geometry already (compact)", json.dumps([{"type": "Title", "text": "Hi", "metadata": {"page_number": 1}}], separators=(",", ":")), False),
        Fixture("not JSON", "partition_pdf took 4.2s\n", False),
    ],
    min_gain=0.5,
)

# ===============================================================================================================
# #770 Collapse near-duplicate chunks across document versions at ingest. Corpus-level: the 'input' is a list of chunks
# serialised as '=== chunk id=ID path=PATH ===' blocks. Chunks of 12+ words whose word 3-shingle Jaccard similarity to the
# canonical chunk is >= 0.9 (exact Jaccard stands in for MinHash/SimHash, which estimate it) are merged: the newest version
# (highest v<N> in the path, else the first in file order) is kept and its header gains 'also=ID|PATH,...' for every
# merged chunk; the others are deleted. Canonicals are compared against canonicals only, so the merged corpus is a fixed point.

_CHUNK_HDR = re.compile(r"^=== chunk id=(\S+) path=(\S+)(?: also=(\S+))? ===$")
JACCARD_NUM, JACCARD_DEN = 9, 10
MIN_WORDS = 12


def _shingles(text: str) -> frozenset:
    w = re.findall(r"\w+", text.lower())
    return frozenset(zip(w, w[1:], w[2:])) if len(w) >= MIN_WORDS else frozenset()


def _version(path: str) -> tuple:
    m = re.search(r"(?<![A-Za-z0-9])v(\d+(?:\.\d+)*)(?![A-Za-z0-9])", path)
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def _parse_chunks(text: str):
    pre, chunks = [], []
    for l in text.split("\n"):
        if (m := _CHUNK_HDR.match(l)):
            chunks.append([m.group(1), m.group(2), m.group(3), []])
        elif chunks:
            chunks[-1][3].append(l)
        else:
            pre.append(l)
    return pre, chunks


def dedupe_chunks(text: str) -> str:
    pre, chunks = _parse_chunks(text)
    if len(chunks) < 2 or any(c[2] is not None for c in chunks) or any(("," in c[0] or "|" in c[0] or "|" in c[1] or "," in c[1]) for c in chunks):
        return text
    sh = [_shingles("\n".join(c[3])) for c in chunks]
    order = sorted(range(len(chunks)), key=lambda i: (tuple(-x for x in _version(chunks[i][1])) if _version(chunks[i][1]) else (1,), i))
    # newest version first; unversioned chunks (version ()) come after versioned ones, in file order
    order = sorted(range(len(chunks)), key=lambda i: (not _version(chunks[i][1]), tuple(-x for x in _version(chunks[i][1])), i))
    reps: list[int] = []
    members: dict[int, list[int]] = {}
    for i in order:
        home = None
        if sh[i]:
            for r in reps:
                inter, union = len(sh[i] & sh[r]), len(sh[i] | sh[r])
                if inter * JACCARD_DEN >= JACCARD_NUM * union:
                    home = r
                    break
        if home is None:
            reps.append(i)
            members[i] = []
        else:
            members[home].append(i)
    if all(not m for m in members.values()):
        return text
    out = list(pre)
    for i, c in enumerate(chunks):
        if i not in members:
            continue
        also = ",".join(f"{chunks[j][0]}|{chunks[j][1]}" for j in sorted(members[i]))
        out.append(f"=== chunk id={c[0]} path={c[1]}" + (f" also={also}" if also else "") + " ===")
        out += c[3]
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def probes_770(text: str) -> dict:
    """Reader view: the inventory of every chunk id and path (merged ones through 'also='), and the versions each section (file#chunk) exists in.
    Which surviving text is the right one cannot be a probe: a mutated corpus may merge chunks of different sections, so the case
    pins that on its fixtures through the claim instead (the canonical is always the highest version)."""
    lines = text.split("\n")
    heads = [(m.group(1), m.group(2), m.group(3), i) for i, l in enumerate(lines) if (m := _CHUNK_HDR.match(l))]
    pairs: list[tuple[str, str]] = []
    for cid, path, also, _ in heads:
        pairs.append((cid, path))
        if also:
            pairs += [tuple(p.split("|", 1)) for p in also.split(",") if "|" in p]
    versions: dict[str, set] = {}
    for _, path in pairs:
        versions.setdefault(path.rsplit("/", 1)[-1], set()).add(_version(path))
    never_older = all(_version(path) >= _version(p.split("|", 1)[1]) for _, path, also, _ in heads if also for p in also.split(",") if "|" in p)
    return {
        "a kept chunk is never older than a chunk merged into it": never_older,
        "inventory of (chunk id, path)": sorted(pairs),
        "versions per section (file#chunk)": sorted((k, sorted(v)) for k, v in versions.items()),
        "preamble": lines[: heads[0][3]] if heads else lines,
    }


def make_chunks() -> str:
    rnd = random.Random(31)
    vocab = ("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda sigma omega tau upsilon phi chi psi ember harbor lantern meadow quartz ribbon saddle timber velvet willow zephyr amber basalt cobalt dune "
             "fjord glacier hollow island jasper kelp lagoon marsh nebula orchid pebble quill reef summit tundra vortex").split()
    topics = {
        "guide.md#0": "install the cli and deploy your first app; run the installer then log in",
        "auth.md#1": "access tokens expire after fifteen minutes and the refresh token rotates on every use of the refresh endpoint",
        "webhooks.md#2": "every webhook delivery carries a signature header computed with hmac over the raw body",
        "limits.md#3": "the rate limit is six hundred requests per minute per key and clients should retry after the delay",
        "paging.md#4": "list endpoints return an opaque cursor for pagination and a page size parameter",
        "orders.md#5": "send an idempotency key so a retried order never creates a duplicate",
        "sandbox.md#6": "the sandbox mirrors production and the data reset runs every night",
        "secrets.md#7": "store each secret in the secret manager and grant the service a read only role",
    }
    docs = []
    chunks = []
    for key, lead in topics.items():
        words = [rnd.choice(vocab) for _ in range(150)]
        base = lead + ". " + " ".join(words)
        wl = base.split(" ")
        v1 = base
        v2w = list(wl)
        v2w[40] = "revised"
        v2 = " ".join(v2w)
        v3w = list(v2w)
        v3w[110] = "updated"
        v3 = " ".join(v3w)
        chunks += [(f"docs/v1/{key}", v1), (f"docs/v2/{key}", v2), (f"docs/v3/{key}", v3)]
    # mirrored READMEs: identical copies in three packages plus one with a one-word difference, unversioned
    readme = "this package is part of the monorepo and is built with the shared toolchain " + " ".join(rnd.choice(vocab) for _ in range(120))
    chunks += [("packages/a/README.md#0", readme), ("packages/b/README.md#0", readme), ("packages/c/README.md#0", readme.replace("shared", "common", 1))]
    # distinct short chunks and one unrelated long chunk
    chunks += [("docs/v3/tiny.md#0", "see the guide."), ("docs/v2/tiny.md#0", "see the guide."), ("docs/v3/changelog.md#9", "release notes " + " ".join(rnd.choice(vocab) for _ in range(90)))]
    rnd.shuffle(chunks)
    out = ["# chunks of docs-repo @ 4f1c8a2"]
    for i, (path, body) in enumerate(chunks):
        out.append(f"=== chunk id=c{i:04d} path={path} ===")
        out.append(body)
    return "\n".join(out) + "\n"


_CHUNKS = make_chunks()
case(
    770,
    claim="In a serialised chunk list, chunks of 12+ words with word 3-shingle Jaccard >= 0.9 to the canonical (newest version, else first) are merged into it: the canonical keeps its text and gains 'also=ID|PATH,...' for each merged chunk, the others are deleted; "
          "every chunk id and path stays listed and the versions each section exists in are unchanged.",
    loses="the text of every merged non-canonical chunk (its differing words, e.g. an old default), and chunks that drift beyond Jaccard 0.9 of the newest are not merged at all",
    encode=dedupe_chunks,
    probes=probes_770,
    fixtures=[
        Fixture("24 versioned chunks (v1-v3 of 8 sections), 4 mirrored README copies, tiny and unrelated chunks", _CHUNKS, True),
        Fixture("same, as if truncated mid-corpus", _CHUNKS[: len(_CHUNKS) // 2], True),
        Fixture("chunks with nothing similar", "\n".join(f"=== chunk id=c{i} path=docs/v1/s{i}.md#0 ===\n" + " ".join(f"word{i}x{j}" for j in range(40)) for i in range(5)) + "\n", False),
        Fixture("one chunk", "=== chunk id=c1 path=docs/v1/a.md#0 ===\n" + "the same words repeated here for twenty words at least okay " * 3 + "\n", False),
        Fixture("already merged corpus", _CHUNKS.replace("=== chunk id=c0001 path=", "=== chunk id=c0001 also=c9|x/y path=", 1).replace(" also=c9|x/y path=", " path=", 1).replace("=== chunk id=c0001 path=docs", "=== chunk id=c0001 path=docs", 1), None),
        Fixture("header with a comma in the id (refused)", "=== chunk id=a,b path=docs/v1/a.md#0 ===\n" + "same text for a dozen words or more here ok now " * 2 + "\n=== chunk id=c path=docs/v2/a.md#0 ===\n" + "same text for a dozen words or more here ok now " * 2 + "\n", False),
    ],
    min_gain=0.3,
)

# ===============================================================================================================
# #1020 Fold identical token substitutions across git diff hunks. A hunk is a pure substitution when every changed line
# pairs one-to-one (n removed lines, then n added lines) with an added line that differs only at word tokens equal to the
# same OLD -> NEW. A substitution found in 3+ hunks is emitted once as 's/OLD/NEW/ path:line,line ...' (new-side line numbers of
# the changed lines) and those hunks (with their context lines) are removed; a file whose hunks all fold disappears from the
# diff. Exceptions stay as ordinary hunks, byte for byte. The old line is the tree line with NEW replaced by OLD, the new line
# is the tree line, so `git diff` / the working tree recovers anything dropped.

_TOK = re.compile(r"\w+|[^\w\s]|\s+")
_SUBST_HEAD = re.compile(r"^folded substitutions \((\d+) changed lines in (\d+) files; `git diff` shows the original hunks\):$")
_SUBST_LINE = re.compile(r"^s/(\w+)/(\w+)/ (\S.*)$")
MIN_HUNKS = 3


def _hunks_of(sec: list[str]):
    """-> (header lines, [(hunk lines)]) if the section is well-formed (counts match each @@ header), else None."""
    i = 1
    while i < len(sec) and not sec[i].startswith("@@"):
        if not sec[i].startswith(("index ", "new file mode ", "deleted file mode ", "old mode ", "new mode ", "--- ", "+++ ", "similarity index ", "rename from ", "rename to ", "Binary files ")):
            return None
        i += 1
    head, hunks = sec[:i], []
    while i < len(sec):
        h = _HUNK.match(sec[i])
        if not h:
            return None
        old_n, new_n = int(h.group(2) if h.group(2) is not None else 1), int(h.group(4) if h.group(4) is not None else 1)
        j = i + 1
        while old_n > 0 or new_n > 0:
            if j >= len(sec) or sec[j][:1] not in (" ", "-", "+"):
                return None
            c = sec[j][0]
            old_n -= c in " -"
            new_n -= c in " +"
            if old_n < 0 or new_n < 0:
                return None
            j += 1
        if j < len(sec) and sec[j].startswith("\\"):
            return None
        hunks.append(sec[i:j])
        i = j
    return head, hunks


def _pure_subst(hunk: list[str]):
    """-> ((old, new), [new-side line numbers of changed lines]) when the hunk is a pure token substitution, else None."""
    h = _HUNK.match(hunk[0])
    new_ln = int(h.group(3))
    pair: tuple[str, str] | None = None
    lines_changed: list[int] = []
    i = 1
    while i < len(hunk):
        if hunk[i][0] == " ":
            new_ln += 1
            i += 1
            continue
        rem, add = [], []
        while i < len(hunk) and hunk[i][0] == "-":
            rem.append(hunk[i][1:])
            i += 1
        while i < len(hunk) and hunk[i][0] == "+":
            add.append(hunk[i][1:])
            i += 1
        if not rem or len(rem) != len(add):
            return None
        for a, b in zip(rem, add):
            ta, tb = _TOK.findall(a), _TOK.findall(b)
            if len(ta) != len(tb) or "".join(ta) != a or "".join(tb) != b:
                return None
            diffs = {(x, y) for x, y in zip(ta, tb) if x != y}
            if not diffs or len(diffs) != 1:
                return None
            (o, n), = diffs
            if not (re.fullmatch(r"\w+", o) and re.fullmatch(r"\w+", n)) or (pair is not None and pair != (o, n)):
                return None
            pair = (o, n)
        lines_changed += list(range(new_ln, new_ln + len(add)))
        new_ln += len(add)
    return (pair, lines_changed) if pair else None


def _section_path(sec: list[str]) -> str | None:
    m = _DIFF_HDR.match(sec[0])
    return m.group(2) if m and m.group(1) == m.group(2) and " " not in m.group(2) and ":" not in m.group(2) else None


def fold_substitutions(text: str) -> str:
    trail = text.endswith("\n")
    body = text[:-1] if trail else text
    if not body.startswith("diff --git ") or any(l.startswith("folded substitutions (") for l in body.split("\n")):
        return text
    secs = _split_sections(body)
    parsed = []
    groups: dict[tuple, list] = defaultdict(list)
    for si, sec in enumerate(secs):
        p = _hunks_of(sec) if _section_path(sec) else None
        parsed.append(p)
        if p:
            for hi, hk in enumerate(p[1]):
                r = _pure_subst(hk)
                if r:
                    groups[r[0]].append((si, hi, r[1]))
    fold = {k: v for k, v in groups.items() if len(v) >= MIN_HUNKS}
    if not fold:
        return text
    folded_at = {(si, hi) for v in fold.values() for si, hi, _ in v}
    out: list[str] = []
    for si, sec in enumerate(secs):
        p = parsed[si]
        if p is None or not any((si, hi) in folded_at for hi in range(len(p[1]))):
            out += sec
            continue
        kept = [hk for hi, hk in enumerate(p[1]) if (si, hi) not in folded_at]
        if kept:
            out += p[0] + [l for hk in kept for l in hk]
    summary = []
    nlines = nfiles = 0
    for (o, n), occ in fold.items():
        per_file: dict[str, list[int]] = {}
        for si, hi, lns in occ:
            per_file.setdefault(_section_path(secs[si]), []).extend(lns)
        nlines += sum(len(v) for v in per_file.values())
        nfiles += len(per_file)
        summary.append(f"s/{o}/{n}/ " + " ".join(f"{pth}:{','.join(map(str, lns))}" for pth, lns in per_file.items()))
    out += [f"folded substitutions ({nlines} changed lines in {nfiles} files; `git diff` shows the original hunks):"] + summary
    res = "\n".join(out) + ("\n" if trail else "")
    return res if len(res) < len(text) else text


def probes_1020(text: str) -> dict:
    """Reader view: per file the hunks that were not folded (verbatim), and every substitution with its (path, new-side line) locations."""
    lines = text.split("\n")
    trail_empty = lines and lines[-1] == ""
    if trail_empty:
        lines = lines[:-1]
    cut = next((i for i, l in enumerate(lines) if _SUBST_HEAD.match(l)), None)
    diff_lines, summary_lines = (lines[:cut], lines[cut + 1:]) if cut is not None else (lines, [])
    secs = _split_sections("\n".join(diff_lines)) if diff_lines else []
    substs: dict[tuple, list] = defaultdict(list)
    kept_sections = []
    parsed = [(_hunks_of(sec) if sec and sec[0].startswith("diff --git ") and _section_path(sec) else None, sec) for sec in secs]
    pure_counts: Counter = Counter()
    for p, sec in parsed:
        if p:
            for hk in p[1]:
                r = _pure_subst(hk)
                if r:
                    pure_counts[r[0]] += 1
    for p, sec in parsed:
        if p is None:
            kept_sections.append((tuple(sec), ()))
            continue
        keep = []
        for hk in p[1]:
            r = _pure_subst(hk) if cut is None else None
            if r and pure_counts[r[0]] >= MIN_HUNKS:
                path = _section_path(sec)
                substs[r[0]] += [(path, n) for n in r[1]]
            else:
                keep.append(tuple(hk))
        if keep or not p[1]:
            kept_sections.append((tuple(p[0]), tuple(keep)))
    for l in summary_lines:
        m = _SUBST_LINE.match(l)
        if not m:
            continue
        for tok in m.group(3).split(" "):
            path, _, nums = tok.rpartition(":")
            substs[(m.group(1), m.group(2))] += [(path, int(n)) for n in nums.split(",") if n]
    return {
        "sections with the hunks that remain (headers, hunks)": kept_sections,
        "substitutions with (path, line) locations": sorted((k, sorted(v)) for k, v in substs.items()),
    }


_RS = _split_sections(RENAME_DIFF.rstrip("\n"))
_RS_BY = {s[0].split(" b/")[-1]: "\n".join(s) for s in _RS}
case(
    1020,
    claim="Hunks that are a pure OLD -> NEW word substitution (every changed line pairs with one that differs only there) and occur in 3+ hunks become 's/OLD/NEW/ path:line,...' (new-side line numbers) with the hunks and their context removed; "
          "hunks that do anything else stay byte for byte, files whose hunks all fold vanish, and the exact set of (path, line) locations per substitution is unchanged.",
    loses="the text of the changed lines and the context lines of folded hunks (the working tree has them: the new line is the tree line, the old line is the tree line with NEW replaced by OLD), blob ids and file headers of fully folded files",
    encode=fold_substitutions,
    probes=probes_1020,
    fixtures=[
        Fixture("real rename: Response -> HTTPResponse over 17 files, two non-pure hunks among 50 changed lines", RENAME_DIFF, True),
        Fixture("the exception hunks first (_models.py, _api.py) then the rest", "\n".join([_RS_BY["httpcore2/_models.py"], _RS_BY["httpcore2/_api.py"]] + [v for k, v in _RS_BY.items() if k not in ("httpcore2/_models.py", "httpcore2/_api.py")]) + "\n", True),
        Fixture("six files of the rename only", "\n".join(list(_RS_BY.values())[2:8]) + "\n", True),
        Fixture("two pure hunks only (below the threshold)", _RS_BY["httpcore2/__init__.py"] + "\n", False),
        Fixture("generated-files diff with no substitution", GEN_DIFF, False),
        Fixture("not a diff", "Compiling core v0.4.2\n", False),
        Fixture("diff that already has a folded block", RENAME_DIFF + "folded substitutions (2 changed lines in 1 files; `git diff` shows the original hunks):\ns/a/b/ x.py:1,2\n", None),
    ],
    min_gain=0.5,
)

# ===============================================================================================================
# #1104 Transcript chaptering: segment a long timestamped transcript by lexical shift (a TextTiling-style cosine score between
# the windows either side of each line, depth-scored, minimum 8 lines per chapter) and emit '[mm:ss] Speaker: lead sentence, N
# words elided' per chapter under a one-line header (chapters, words, time span, speakers). The original stays on disk to grep.
# Transcripts under 40 lines or 500 words are left alone. The probes pin that the outline is a faithful rendering of the
# segmentation (stamps, lead sentences, word accounting, span, speakers); the segmentation itself is reused by the probe for the
# original text, so the case does NOT test that the chapter boundaries are good; that is the idea's 'ask 10 questions' study.

_T_LINE = re.compile(r"^\[(?:(\d+):)?(\d{2}):(\d{2})\] (.*)$")
_T_SPEAKER = re.compile(r"^([A-Z][A-Za-z.' -]{0,30}): (.*)$")
_T_OUT = re.compile(r"^\[(?:(\d+):)?(\d{2}):(\d{2})\] (.*), (\d+) words elided$")
_T_HEAD = re.compile(r"^outline: (\d+) chapters, (\d+) words, (\S+)-(\S+), speakers (\[.*\])$")
_STOP = set("this that with have from they their there which would about these those then than also just like some what when will your more very into only over such been were them being because while where after before could should other really going actually".split())
TT_WINDOW, TT_MIN_LINES = 4, 8


def _parse_transcript(text: str):
    entries: list[list] = []  # [seconds, speaker, text]
    for l in text.split("\n"):
        m = _T_LINE.match(l)
        if m:
            body = m.group(4)
            sp = _T_SPEAKER.match(body)
            entries.append([_secs(m.group(1), m.group(2), m.group(3)), sp.group(1) if sp else None, sp.group(2) if sp else body])
        elif entries and l.strip():
            entries[-1][2] += " " + l.strip()
    return entries


def _bag(t: str) -> Counter:
    return Counter(w for w in re.findall(r"[a-z]{4,}", t.lower()) if w not in _STOP)


def _cos(a: Counter, b: Counter) -> float:
    num = sum(v * b.get(k, 0) for k, v in a.items())
    den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return num / den if den else 0.0


def _segment(entries) -> list[int]:
    """Start indexes of chapters (always including 0)."""
    n, w = len(entries), TT_WINDOW
    bags = [_bag(e[2]) for e in entries]
    gaps = list(range(w, n - w + 1))
    sims = []
    for g in gaps:
        left, right = Counter(), Counter()
        for b in bags[g - w:g]:
            left.update(b)
        for b in bags[g:g + w]:
            right.update(b)
        sims.append(round(_cos(left, right), 6))
    depth = []
    for k, s in enumerate(sims):
        lo, hi = max(0, k - 6), min(len(sims), k + 7)
        depth.append(round(max(sims[lo:k + 1]) - s + max(sims[k:hi]) - s, 6))
    if not depth:
        return [0]
    mean = sum(depth) / len(depth)
    sd = math.sqrt(sum((d - mean) ** 2 for d in depth) / len(depth))
    cut = mean + 0.5 * sd
    bounds = [0]
    for k, g in enumerate(gaps):
        if depth[k] >= cut and depth[k] == max(depth[max(0, k - 4):k + 5]) and g - bounds[-1] >= TT_MIN_LINES and n - g >= TT_MIN_LINES:
            bounds.append(g)
    return bounds


def _lead(text: str) -> str:
    m = re.search(r"[.!?](?:\s|$)", text)
    return (text[: m.end()].strip() if m else text.strip())[:160]


def _chapters(entries):
    starts = _segment(entries)
    out = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(entries)
        text = " ".join(x[2] for x in entries[s:e])
        lead = _lead(entries[s][2])
        out.append((entries[s][0], entries[s][1], lead, len(text.split()) - len(lead.split()), len(text.split())))
    return out


def chapter_transcript(text: str) -> str:
    entries = _parse_transcript(text)
    if len(entries) < 40 or sum(len(e[2].split()) for e in entries) < 500 or any(_T_OUT.match(l) or _T_HEAD.match(l) for l in text.split("\n")):
        return text
    if any('"' in (e[1] or "") or "\\" in (e[1] or "") for e in entries):
        return text
    chs = _chapters(entries)
    speakers = sorted({e[1] for e in entries if e[1]})
    total = sum(e[2].split().__len__() for e in entries)
    out = [f"outline: {len(chs)} chapters, {total} words, {_stamp(entries[0][0])[1:-1]}-{_stamp(max(e[0] for e in entries))[1:-1]}, speakers {json.dumps(speakers)}"]
    for st, sp, lead, elided, _ in chs:
        out.append(f"{_stamp(st)} " + (f"{sp}: " if sp else "") + f"{lead}, {elided} words elided")
    res = "\n".join(out) + ("\n" if text.endswith("\n") else "")
    return res if len(res) < len(text) else text


def probes_1104(text: str) -> dict:
    lines = text.split("\n")
    head = next((m for l in lines if (m := _T_HEAD.match(l))), None)
    if head is not None and all(_T_LINE.match(l) is None or _T_OUT.match(l) for l in lines if l):
        speakers = json.loads(head.group(5))
        chs = []
        for l in lines:
            m = _T_OUT.match(l)
            if not m:
                continue
            rest, sp = m.group(4), None
            for name in speakers:
                if rest.startswith(name + ": "):
                    sp, rest = name, rest[len(name) + 2:]
                    break
            chs.append((_secs(m.group(1), m.group(2), m.group(3)), sp, rest, int(m.group(5)), len(rest.split()) + int(m.group(5))))
        span = (head.group(3), head.group(4))
        total = int(head.group(2))
        accounted = sum(c[4] for c in chs)
    else:
        entries = _parse_transcript(text)
        if len(entries) < 40 or sum(len(e[2].split()) for e in entries) < 500 or any(_T_OUT.match(l) or _T_HEAD.match(l) for l in lines):
            return {"outline": None, "text": text if not entries else None, "entries": len(entries)}
        chs = _chapters(entries)
        speakers = sorted({e[1] for e in entries if e[1]})
        span = (_stamp(entries[0][0])[1:-1], _stamp(max(e[0] for e in entries))[1:-1])
        total = sum(len(e[2].split()) for e in entries)
        accounted = sum(c[4] for c in chs)
    return {
        "chapters (start, speaker, lead sentence, words elided)": [c[:4] for c in chs],
        "words in the transcript and words accounted for by the chapters": (total, accounted),
        "time span": span,
        "speakers": speakers,
    }


def make_lecture(lines_per_topic: int = 30, with_speakers: bool = True, seed: int = 17, t0: int = 0) -> str:
    rnd = random.Random(seed)
    topics = {
        "indexing": "index btree leaf page scan query planner selectivity cardinality composite prefix covering".split(),
        "caching": "cache header expiry revalidate etag proxy origin invalidation stale browser directive freshness".split(),
        "scheduling": "scheduler pod node taint toleration affinity kubelet replica deployment resource limit eviction".split(),
        "gradient": "gradient descent learning rate loss minibatch momentum epoch weights backward activation overfitting".split(),
    }
    filler = "so now what we want to see here is that the idea is quite simple and we will come back to it in a moment".split()
    out = []
    t = t0
    for ti, (name, vocab) in enumerate(topics.items()):
        for k in range(lines_per_topic):
            sents = []
            for _ in range(rnd.randint(2, 3)):
                a, b, c = rnd.sample(vocab, 3)
                sents.append(f"The {a} of a {b} depends mostly on the {c} and {rnd.choice(filler)} {rnd.choice(filler)}.")
            if k == 0:
                sents[0] = f"Now let's move on to {name}, which is the next big topic."
            sp = ("Maria" if (k // 5) % 2 == 0 else "Joel") + ": " if with_speakers else ""
            out.append(f"{_stamp(t)} {sp}{' '.join(sents)}")
            t += rnd.randint(22, 38)
    return "\n".join(out) + "\n"


_LECTURE = make_lecture()
case(
    1104,
    claim="A timestamped transcript of 40+ lines is segmented by lexical shift (TextTiling-style, 8+ lines per chapter) and replaced by a one-line header plus '[mm:ss] Speaker: lead sentence, N words elided' per chapter; the chapter starts and leads, "
          "the word count (lead words plus elided words equal the transcript's), the time span and the speaker list are those of the segmentation of the original. Short transcripts are left alone.",
    loses="everything except each chapter's first sentence and word count (the original must be grepped or re-read for details), the stamps of lines inside a chapter, and any chapter boundary the lexical shift misses",
    encode=chapter_transcript,
    probes=probes_1104,
    fixtures=[
        Fixture("4-topic lecture, 120 lines (about 3,800 words) with two speakers", _LECTURE, True),
        Fixture("same without speaker labels, starting at 1:02:05", make_lecture(30, False, t0=3725), True),
        Fixture("longer lecture, 4 x 45 lines", make_lecture(45, True, seed=3), True),
        Fixture("short meeting transcript (12 lines)", "\n".join(_LECTURE.split("\n")[:12]) + "\n", False),
        Fixture("long text without stamps", "plain paragraph of words " * 400 + "\n", False),
        Fixture("input that already is an outline", "outline: 1 chapters, 600 words, 00:00-01:00, speakers []\n[00:00] intro sentence here., 590 words elided\n" + _LECTURE, None),
    ],
    min_gain=0.5,
)
