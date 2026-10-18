using System.Globalization;
using System.Numerics;
using System.Text.RegularExpressions;
using QuantAssistant.Core.Portfolio;
using QuantAssistant.Core.Errors;

namespace QuantAssistant.Api.Services;

/// <summary>Confirmed trades only. Keep decimal strings exact until all storage bounds are checked.</summary>
public static class LedgerCsvInput
{
    public sealed record Trade(string Symbol,string AssetType,string Action,string Quantity,string Price,string Fees,string OccurredAt,string ExternalId,string? CashSymbol=null,string? Note=null,int Row=0);
    public sealed record Batch(Trade[] Trades,string Reason);
    public static LedgerReplayMigrationService.HistoricalBody[] Validate(Batch batch)
    {
        if(batch.Trades is null || batch.Trades.Length is <1 or >1000 || string.IsNullOrWhiteSpace(batch.Reason) || batch.Reason.Length>1000)
            throw new ApplicationFailure("invalid_request",422,"每批需1至1000笔已确认成交，并填写核对依据。");
        var result=new List<LedgerReplayMigrationService.HistoricalBody>();var ids=new HashSet<string>(StringComparer.Ordinal);DateTimeOffset? previous=null;
        foreach(var (trade,index) in batch.Trades.Select((trade,index)=>(trade,index)))
        {
            var label=trade?.Row>0?$"CSV 第 {trade.Row} 行":$"第 {index+1} 笔";
            try
            {
                if(trade is null || !Regex.IsMatch(trade.Symbol??"","^[A-Za-z0-9._:-]{1,32}$") || string.IsNullOrWhiteSpace(trade.ExternalId) || trade.ExternalId.Length>128 || trade.Note?.Length>500 || trade.CashSymbol?.Length>32)
                    throw new ArgumentException("代码、成交编号或备注无效。");
                if(!ids.Add(trade.ExternalId.Trim()))throw new ArgumentException("批次内成交编号重复。");
                if(!Regex.IsMatch(trade.OccurredAt??"",@"^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d:[0-5]\d(\.\d{1,3})?(Z|[+-](0\d|1[0-3]):[0-5]\d|[+-]14:00)$") || !DateTimeOffset.TryParse(trade.OccurredAt,CultureInfo.InvariantCulture,DateTimeStyles.None,out var instant))
                    throw new ArgumentException("确认时间须为真实日期及含时区的 ISO 时间，毫秒最多三位。");
                if(instant>DateTimeOffset.UtcNow)throw new ArgumentException("不能导入未来成交。");
                if(previous is not null && instant<=previous)throw new ArgumentException("成交须按真实时间顺序排列；同时发生的成交需先核对日内顺序。");
                previous=instant;
                var body=new LedgerReplayMigrationService.HistoricalBody(trade.Symbol!,trade.AssetType,trade.Action,Decimal(trade.Quantity,20,8),Decimal(trade.Price,14,6),Decimal(trade.Fees,16,4),instant.ToOffset(TimeSpan.FromHours(8)),trade.ExternalId.Trim(),batch.Reason.Trim(),string.IsNullOrWhiteSpace(trade.CashSymbol)?null:trade.CashSymbol.Trim().ToUpperInvariant(),trade.Note);
                var type=body.AssetType switch {"fund"=>AssetType.Fund,"stock"=>AssetType.Stock,_=>throw new ArgumentException("只支持基金或股票成交。")};
                var action=body.Action switch {"buy"=>Core.Portfolio.Action.Buy,"add"=>Core.Portfolio.Action.Add,"sell"=>Core.Portfolio.Action.Sell,"reduce"=>Core.Portfolio.Action.Reduce,_=>throw new ArgumentException("成交动作无效。")};
                ExactGross(body.Quantity,body.Price);
                var value=TransactionRules.Validate(new TransactionInput(body.Symbol,type,action,body.Quantity,body.Price,body.Fees,body.OccurredAt,body.ExternalId,"historical-manual",body.CashSymbol,body.Note));
                if(Math.Abs(value.GrossAmount)>=100000000000000000000m || Math.Abs(value.CashDelta)>=100000000000000000000m || decimal.Round(value.GrossAmount,8)!=value.GrossAmount || decimal.Round(value.CashDelta,8)!=value.CashDelta)
                    throw new ArgumentException("成交金额超出范围或乘积超过八位小数，请核对确认单；系统不会自动舍入。");
                result.Add(body);
            }
            catch(Exception error) when(error is ArgumentException or OverflowException)
            {throw new ApplicationFailure("invalid_request",422,$"{label}：{(error is OverflowException?"成交金额超出范围。":error.Message)}");}
        }
        return result.ToArray();
    }
    private static decimal Decimal(string? text,int whole,int scale)
    {
        if(text is null || !Regex.IsMatch(text,$"^[0-9]{{1,{whole}}}(\\.[0-9]{{1,{scale}}})?$") || !decimal.TryParse(text,NumberStyles.AllowDecimalPoint,CultureInfo.InvariantCulture,out var value))
            throw new ArgumentException($"数值须为普通非负小数，整数最多{whole}位、小数最多{scale}位；不支持科学计数法。");
        return value;
    }
    private static void ExactGross(decimal quantity,decimal price)
    {
        static (BigInteger Coefficient,int Scale) Parts(decimal value)
        {var bits=decimal.GetBits(value);return ((new BigInteger((uint)bits[2])<<64)+(new BigInteger((uint)bits[1])<<32)+(uint)bits[0],(bits[3]>>16)&255);}
        var left=Parts(quantity);var right=Parts(price);var product=left.Coefficient*right.Coefficient;var scale=left.Scale+right.Scale;
        if(product>=BigInteger.Pow(10,20+scale) || (scale>8 && product%BigInteger.Pow(10,scale-8)!=0))
            throw new ArgumentException("成交金额超出范围或乘积超过八位小数，请核对确认单；系统不会自动舍入。");
    }
}
