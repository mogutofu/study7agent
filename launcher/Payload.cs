// Study7Agent 启动器 —— 打包成单文件 exe 时，程序树（python / webui / 内核）
// 以 tar.gz 的形式追加在本 exe 尾部；这个文件负责把那一段解出来。
//
// 尾部结构（从后往前读）：
//     [8 字节 payload 长度][8 字节魔数][4 字节戳长度][戳][payload ...][exe ...]
// 固定尾部 20 字节 + 长度可变的戳，读两次就能定位 payload，不需要知道 exe 自己多大，
// 也就避开了"编译前要先把 exe 大小写进源码"那种循环。
//
// 语言级别是 C# 3.0（Win7 自带的 csc 3.5 只能编到 3.0），所以：
// 不许用 async/await、字符串内插、?.、nameof、元组。

using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;

// 注意：这里**不开 namespace**，和 Study7Agent.cs（全局命名空间里的 Program）保持一致；
// 也不行用 Action<T1,T2> —— /noconfig 下引用的是 CLR 2.0 的 mscorlib，
// 那个 mscorlib 里只有 Action<T>，两参数的 Action 在 System.Core（.NET 3.5）里，编译不过。

/// <summary>解包进度回调：(已解字节, 总字节)。</summary>
internal delegate void ProgressHandler(long current, long total);

internal static class Payload
{
        public const string Magic = "W7APAYL1";
        private const int TailSize = 20;   // 8 长度 + 8 魔数 + 4 戳长度

        public sealed class Info
        {
            public string Stamp = "";
            public long Offset;
            public long Length;
            public long ExeLength;
        }

        /// <summary>读尾部信息；不是单文件版（没有 payload）时返回 null。</summary>
        public static Info Read(string exePath)
        {
            var file = new FileInfo(exePath);
            long total = file.Length;
            if (total < TailSize + 32)
            {
                return null;
            }
            byte[] tail = new byte[TailSize];
            using (var stream = new FileStream(exePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            {
                stream.Seek(total - TailSize, SeekOrigin.Begin);
                if (!ReadExact(stream, tail, TailSize))
                {
                    return null;
                }
            }
            long payloadLength = BitConverter.ToInt64(tail, 0);
            string magic = Encoding.ASCII.GetString(tail, 8, 8);
            int stampLength = BitConverter.ToInt32(tail, 16);
            if (magic != Magic || payloadLength <= 0 || stampLength < 0 || stampLength > 4096)
            {
                return null;
            }
            long trailer = 20L + stampLength;
            if (total < trailer + payloadLength + 1024)
            {
                return null;   // 至少要比 payload 多出一点，说明 exe 本体在里面
            }
            var info = new Info();
            info.ExeLength = total;
            info.Length = payloadLength;
            info.Offset = total - trailer - payloadLength;
            if (stampLength > 0)
            {
                byte[] buffer = new byte[stampLength];
                using (var stream = new FileStream(exePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                {
                    stream.Seek(total - trailer, SeekOrigin.Begin);
                    if (ReadExact(stream, buffer, stampLength))
                    {
                        info.Stamp = Encoding.UTF8.GetString(buffer);
                    }
                }
            }
            return info;
        }

        private static bool ReadExact(Stream stream, byte[] buffer, int count)
        {
            int done = 0;
            while (done < count)
            {
                int got = stream.Read(buffer, done, count - done);
                if (got <= 0)
                {
                    return false;
                }
                done += got;
            }
            return true;
        }

        /// <summary>已经解好过没有（.complete 标记）。</summary>
        public static bool IsExtracted(string destDir)
        {
            return File.Exists(Path.Combine(destDir, ".complete"));
        }

        /// <summary>
        /// 把 payload 解到 destDir。已经解好（有 .complete 标记）就直接返回。
        /// progress 参数：(已解字节, 总字节)。
        /// </summary>
        public static void Extract(string exePath, Info info, string destDir, ProgressHandler progress)
        {
            string done = Path.Combine(destDir, ".complete");
            if (File.Exists(done))
            {
                return;
            }
            string temp = destDir + ".tmp";
            if (Directory.Exists(temp))
            {
                Directory.Delete(temp, true);
            }
            Directory.CreateDirectory(temp);
            try
            {
                using (var file = new FileStream(exePath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                {
                    file.Seek(info.Offset, SeekOrigin.Begin);
                    using (var bounded = new BoundedStream(file, info.Length))
                    using (var gzip = new GZipStream(bounded, CompressionMode.Decompress))
                    {
                        ReadTar(gzip, temp, info.Length, progress);
                    }
                }
                File.WriteAllText(Path.Combine(temp, ".complete"), info.Stamp, Encoding.UTF8);
            }
            catch
            {
                try { Directory.Delete(temp, true); } catch { }
                throw;
            }
            if (Directory.Exists(destDir))
            {
                Directory.Delete(destDir, true);
            }
            Directory.CreateDirectory(Path.GetDirectoryName(destDir));
            Directory.Move(temp, destDir);
        }

        /// <summary>
        /// 极简 tar 读取：只认文件、目录和 GNU 长名（typeflag L / K）。
        /// Python 端用 GNU_FORMAT 打包，所以不会出现 pax 扩展头。
        /// </summary>
        private static void ReadTar(Stream tar, string root, long totalPayload, ProgressHandler progress)
        {
            byte[] header = new byte[512];
            string pendingName = null;
            long consumed = 0;
            while (true)
            {
                if (!ReadExact(tar, header, 512))
                {
                    break;
                }
                consumed += 512;
                bool empty = true;
                for (int index = 0; index < 512; index++)
                {
                    if (header[index] != 0)
                    {
                        empty = false;
                        break;
                    }
                }
                if (empty)
                {
                    break;
                }
                string name = pendingName ?? ReadString(header, 0, 100);
                pendingName = null;
                string prefix = ReadString(header, 345, 155);
                if (prefix.Length > 0)
                {
                    name = prefix + "/" + name;
                }
                long size = ReadOctal(header, 124, 12);
                char type = (char)header[156];
                if (type == 'L' || type == 'K')
                {
                    // GNU 长名：这一段的内容就是下一条记录的名字
                    byte[] raw = new byte[size];
                    if (!ReadExact(tar, raw, (int)size))
                    {
                        break;
                    }
                    consumed += size;
                    consumed += SkipPadding(tar, size);
                    pendingName = Encoding.UTF8.GetString(raw).TrimEnd('\0');
                    continue;
                }
                if (size > 0 && (type == '0' || type == '\0' || type == '7'))
                {
                    string target = Path.Combine(root, name.Replace('/', Path.DirectorySeparatorChar));
                    string folder = Path.GetDirectoryName(target);
                    if (!string.IsNullOrEmpty(folder) && !Directory.Exists(folder))
                    {
                        Directory.CreateDirectory(folder);
                    }
                    using (var output = new FileStream(target, FileMode.Create, FileAccess.Write, FileShare.None))
                    {
                        CopyExact(tar, output, size);
                    }
                    consumed += size;
                }
                else if (type == '5')
                {
                    string folder = Path.Combine(root, name.Replace('/', Path.DirectorySeparatorChar));
                    if (!Directory.Exists(folder))
                    {
                        Directory.CreateDirectory(folder);
                    }
                }
                else
                {
                    SkipExact(tar, size);
                    consumed += size;
                }
                consumed += SkipPadding(tar, size);
                if (progress != null)
                {
                    progress(consumed, totalPayload);
                }
            }
        }

        private static void CopyExact(Stream input, Stream output, long count)
        {
            byte[] buffer = new byte[128 * 1024];
            long left = count;
            while (left > 0)
            {
                int want = (int)Math.Min(buffer.Length, left);
                int got = input.Read(buffer, 0, want);
                if (got <= 0)
                {
                    throw new IOException("payload 提前结束");
                }
                output.Write(buffer, 0, got);
                left -= got;
            }
        }

        private static void SkipExact(Stream stream, long count)
        {
            byte[] buffer = new byte[64 * 1024];
            long left = count;
            while (left > 0)
            {
                int got = stream.Read(buffer, 0, (int)Math.Min(buffer.Length, left));
                if (got <= 0)
                {
                    throw new IOException("payload 提前结束");
                }
                left -= got;
            }
        }

        private static int SkipPadding(Stream stream, long size)
        {
            int pad = (int)((512 - (size % 512)) % 512);
            if (pad > 0)
            {
                SkipExact(stream, pad);
            }
            return pad;
        }

        private static string ReadString(byte[] block, int offset, int length)
        {
            int end = offset;
            while (end < offset + length && block[end] != 0)
            {
                end++;
            }
            return Encoding.UTF8.GetString(block, offset, end - offset);
        }

        private static long ReadOctal(byte[] block, int offset, int length)
        {
            long value = 0;
            for (int index = offset; index < offset + length; index++)
            {
                byte current = block[index];
                if (current == 0 || current == ' ')
                {
                    if (value > 0)
                    {
                        break;
                    }
                    continue;
                }
                if (current < '0' || current > '7')
                {
                    break;
                }
                value = value * 8 + (current - '0');
            }
            return value;
        }
    }

    /// <summary>把底层流限制在前 count 个字节内——gzip 后面还跟着尾部信息，不能让它读到。</summary>
    internal sealed class BoundedStream : Stream
    {
        private readonly Stream inner;
        private readonly long limit;
        private long position;

        public BoundedStream(Stream inner, long limit)
        {
            this.inner = inner;
            this.limit = limit;
        }

        public override bool CanRead { get { return true; } }
        public override bool CanSeek { get { return false; } }
        public override bool CanWrite { get { return false; } }
        public override long Length { get { return limit; } }

        public override long Position
        {
            get { return position; }
            set { throw new NotSupportedException(); }
        }

        public override int Read(byte[] buffer, int offset, int count)
        {
            long left = limit - position;
            if (left <= 0)
            {
                return 0;
            }
            int got = inner.Read(buffer, offset, (int)Math.Min(count, left));
            if (got > 0)
            {
                position += got;
            }
            return got;
        }

        public override void Flush() { }
        public override long Seek(long offset, SeekOrigin origin) { throw new NotSupportedException(); }
        public override void SetLength(long value) { throw new NotSupportedException(); }
        public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
    }
